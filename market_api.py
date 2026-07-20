"""
거래소 시세 조회 (PA 공식 API + arsha.io 폴백) 및 아이템 DB.

ITEM_LIST는 여기서 관리하는 단일 소스입니다. 다른 모듈(예: 아이템DB 갱신 명령어를 담을
admin_util cog)에서 이 dict를 갱신하려면 재할당하지 말고 반드시 .update()를 쓰세요.
dict는 참조로 공유되므로 .update()만 하면 이 모듈을 import한 모든 곳에 즉시 반영됩니다.
"""
import asyncio
import json
import os

import aiohttp
from datetime import datetime

from config import KST, ITEM_DB_FILE
from db import get_db
from data.game_data import FALLBACK_PRICES, SOV_RECIPES, SOV_WEAPON_NAME_PATTERNS
from data.item_data import HARDCODED_ITEMS

ITEM_LIST: dict[str, int] = {}


def load_local_backup() -> str:
    """
    네트워크 요청 없이 items_v2.json만 즉시 로드합니다 (동기 함수).
    봇 시작 시 이것만 호출해서 arsha dump 성공/실패와 무관하게 바로 뜨도록 합니다.
    실제 arsha dump 갱신은 refresh_item_dump_live()가 담당 (수동 명령어 / 매일 자동 루프).
    """
    if os.path.exists(ITEM_DB_FILE):
        with open(ITEM_DB_FILE, "r", encoding="utf-8") as f:
            backup = json.load(f)
        ITEM_LIST.update(backup)
        ITEM_LIST.update(HARDCODED_ITEMS)
        msg = f"↩️ 로컬 백업({ITEM_DB_FILE}) 즉시 로드: {len(backup)}개. 현재 ITEM_LIST 총 {len(ITEM_LIST)}개"
        print(msg)
        return msg
    else:
        ITEM_LIST.update(HARDCODED_ITEMS)
        msg = f"⚠️ 로컬 백업 없음. HARDCODED {len(HARDCODED_ITEMS)}개만 우선 로드 (곧 /아이템디비갱신 필요)"
        print(msg)
        return msg


async def refresh_item_dump_live(force: bool = False) -> str:
    """
    arsha 라이브 dump를 새로 받아서 ITEM_LIST와 items_v2.json을 갱신합니다.
    - /아이템디비갱신 명령어가 수동 호출
    - item_lookup cog의 daily_item_db_refresh 루프가 매일 자동 호출
    dump 요청이 실패했을 때만 기존 로컬 백업으로 폴백합니다 (ITEM_LIST는 그대로 유지됨).
    반환값: 결과 요약 문자열 (로그/응답용)
    """
    try:
        url = "https://api.arsha.io/util/db/dump?lang=kr"
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        async with aiohttp.ClientSession(headers=headers, connector=aiohttp.TCPConnector(ssl=False)) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status != 200:
                    raise RuntimeError(f"dump API status {response.status}")
                data = await response.json()
                if not isinstance(data, list) or not data:
                    raise RuntimeError(f"dump 응답 비정상: type={type(data)}")

                temp = {}
                skipped = 0
                for item in data:
                    name = item.get("name")
                    id_raw = item.get("id")
                    if not name or id_raw is None:
                        skipped += 1
                        continue
                    iid = int(id_raw)
                    if name not in temp or iid < temp[name]:
                        temp[name] = iid
                ITEM_LIST.update(temp)
                ITEM_LIST.update(HARDCODED_ITEMS)

                with open(ITEM_DB_FILE, "w", encoding="utf-8") as f:
                    json.dump(ITEM_LIST, f, ensure_ascii=False, indent=2)

                msg = f"✅ dump 갱신 완료: {len(data)}건 수신, {skipped}건 스킵\n현재 ITEM_LIST 총 {len(ITEM_LIST)}개"
                print(msg)
                return msg

    except Exception as e:
        print(f"⚠️ dump 로드 실패: {type(e).__name__}: {e}")
        if os.path.exists(ITEM_DB_FILE):
            with open(ITEM_DB_FILE, "r", encoding="utf-8") as f:
                backup = json.load(f)
            ITEM_LIST.update(backup)
            ITEM_LIST.update(HARDCODED_ITEMS)
            msg = f"↩️ dump 실패, 로컬 백업 사용: {len(backup)}개 로드됨\n현재 ITEM_LIST 총 {len(ITEM_LIST)}개"
            print(msg)
            return msg
        else:
            msg = f"❌ dump 실패 + 로컬 백업 없음. HARDCODED {len(HARDCODED_ITEMS)}개만 사용 중"
            print(msg)
            ITEM_LIST.update(HARDCODED_ITEMS)
            return msg


def save_to_cache(item_id, sid, price, stock, count):
    try:
        conn = get_db()
        c = conn.cursor()
        now_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
        c.execute("INSERT OR REPLACE INTO item_cache VALUES (?, ?, ?, ?, ?, ?)",
                  (int(item_id), int(sid), int(price), int(stock), int(count), now_str))
        conn.commit()
    except Exception:
        pass


async def get_fallback_value(item_id, sid=0):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT price, stock, count FROM item_cache WHERE item_id=? AND sid=?", (int(item_id), int(sid)))
        row = c.fetchone()
        if row and row[0] is not None and int(row[0]) > 0:
            return int(row[0]), int(row[1]), int(row[2])
    except Exception:
        pass
    return FALLBACK_PRICES.get(int(item_id), (0, 0, 0))


async def fetch_arsha_sublist(item_id):
    url = "https://api.arsha.io/v2/kr/GetWorldMarketSubList"
    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}) as session:
        async with session.get(url, params={"id": item_id, "lang": "kr"}, timeout=15) as response:
            if response.status != 200:
                raise RuntimeError(f"arsha API status {response.status}")
            data = await response.json(content_type=None)
            if isinstance(data, dict):
                for wrap_key in ("data", "items", "result", "list", "subList", "content"):
                    if isinstance(data.get(wrap_key), list):
                        data = data[wrap_key]
                        break
                else:
                    if any(k in data for k in ("basePrice", "price", "sid", "count")):
                        data = [data]
            if not isinstance(data, list):
                raise RuntimeError(f"응답 오류: {type(data)}")
            entries = []
            for row in data:
                try:
                    entries.append({
                        "item_id": int(row.get("mainKey") or row.get("id") or item_id),
                        "min_enhance": int(row.get("sid") or row.get("minEnhance") or 0),
                        "base_price": int(row.get("basePrice") or row.get("price") or 0),
                        "current_stock": int(row.get("count") or row.get("currentStock") or 0),
                        "total_trades": int(row.get("totalTrades") or 0),
                    })
                except Exception:
                    continue
            return entries


async def fetch_pa_sublist(item_id):
    url = "https://trade.kr.playblackdesert.com/Trademarket/GetWorldMarketSubList"
    async with aiohttp.ClientSession(headers={"User-Agent": "BlackDesert", "Content-Type": "application/json"}) as session:
        async with session.post(url, json={"keyType": 0, "mainKey": item_id}, timeout=12) as response:
            if response.status != 200:
                raise RuntimeError(f"PA API status {response.status}")
            raw = await response.read()
            try:
                text = raw.decode("utf-16", errors="surrogatepass").encode("utf-8").decode("utf-8")
            except Exception:
                text = raw.decode("utf-8", errors="ignore")
            data = json.loads(text)
            if data.get("resultCode") != 0:
                raise RuntimeError("PA API 에러")
            entries = []
            for chunk in data.get("resultMsg", "").split('|'):
                parts = chunk.split('-')
                if len(parts) >= 6:
                    try:
                        entries.append({
                            "item_id": int(parts[0]), "min_enhance": int(parts[1]),
                            "base_price": int(parts[3]), "current_stock": int(parts[4]), "total_trades": int(parts[5]),
                        })
                    except Exception:
                        pass
            return entries


async def fetch_market_sublist(item_id):
    """
    공식 웹 엔드포인트(PA, trade.kr.playblackdesert.com)를 메인으로 사용하고,
    실패했을 때만 arsha.io를 fallback으로 씁니다.
    (원래는 arsha.io가 메인이었는데, arsha.io가 자주 응답이 안 되거나 잘못된 값을
    주는 경우가 많아서 순서를 바꿨습니다 — PA는 공식 홈페이지 거래소가 그대로
    쓰는 엔드포인트라 더 안정적입니다.)
    """
    try:
        entries = await fetch_pa_sublist(item_id)
        if entries:
            return entries
        raise RuntimeError("PA 빈결과")
    except Exception:
        try:
            return await fetch_arsha_sublist(item_id)
        except Exception:
            return []


async def get_market_price(item_id, sid=0):
    try:
        item_id, sid = int(item_id), int(sid)
    except Exception:
        return 0, 0, 0
    try:
        entries = await fetch_market_sublist(item_id)
        if not entries:
            return await get_fallback_value(item_id, sid)
        for e in entries:
            if e["min_enhance"] == sid:
                p, s, t = e["base_price"], e["current_stock"], e["total_trades"]
                if p > 0:
                    save_to_cache(item_id, sid, p, s, t)
                    return p, s, t
        if sid == 0 and entries:
            p, s, t = entries[0]["base_price"], entries[0]["current_stock"], entries[0]["total_trades"]
            if p > 0:
                save_to_cache(item_id, sid, p, s, t)
                return p, s, t
        return await get_fallback_value(item_id, sid)
    except Exception:
        return await get_fallback_value(item_id, sid)


async def get_sov_weapon_price(item_key: str):
    req, exc = SOV_WEAPON_NAME_PATTERNS.get(item_key, ([], []))
    if not req:
        return (0, 0, 0)
    m_ids = [iid for n, iid in ITEM_LIST.items() if not any(e in n for e in exc) and all(r in n for r in req)]
    if not m_ids:
        return (0, 0, 0)
    results = await asyncio.gather(*[fetch_market_sublist(i) for i in m_ids], return_exceptions=True)
    bp, bs, bt = 0, 0, 0
    for entries in results:
        if isinstance(entries, Exception) or not entries:
            continue
        for e in entries:
            p = e.get("base_price", 0)
            if p > 0 and (bp == 0 or p < bp):
                bp, bs, bt = p, e.get("current_stock", 0), e.get("total_trades", 0)
    return (bp, bs, bt)


async def fetch_sov_prices(weapon_type: str):
    needed = set(name for _, ings in SOV_RECIPES[weapon_type] for name, _ in ings)
    needed.update(["마력의 파편", "카프라스의 돌"])

    async def _f(n):
        if n in ["황혼의 보석", "태초의 보석"]:
            return (0, 0, 0)
        if n in SOV_WEAPON_NAME_PATTERNS:
            return await get_sov_weapon_price(n)
        for d in [{"name": "마력의 파편", "id": 44195, "sid": 0}, {"name": "카프라스의 돌", "id": 721003, "sid": 0}]:
            if d["name"] == n:
                return await get_market_price(d["id"], d["sid"])
        return (0, 0, 0)

    needed_list = list(needed)
    results = await asyncio.gather(*[_f(n) for n in needed_list], return_exceptions=True)
    prices = {n: res if not isinstance(res, Exception) and res else (0, 0, 0) for n, res in zip(needed_list, results)}
    calc_p = prices.get("마력의 파편", (0,))[0] * 100 + prices.get("카프라스의 돌", (0,))[0] * 20000
    if "황혼의 보석" in needed:
        prices["황혼의 보석"] = (calc_p, 0, 0)
    if "태초의 보석" in needed:
        prices["태초의 보석"] = (calc_p, 0, 0)
    return prices
