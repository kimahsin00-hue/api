"""
아이템 조회 cog.

- 데키아 등불 / 영물의 축복: 효율(불빛·잔재 대비 가격) 랭킹 — admin_util 패널의
  버튼에서 build_dekia_ranking_embed() / build_bless_ranking_embed()를 바로 호출하면 됩니다.
- 펄 의상: PearlTimeView (거래량 추이 조회) + pearl_tracker 루프 (10분마다 수집)
- /아이템디버그, /아이템디비갱신 (관리자 전용)

원본에는 '/아이템디비갱신' 명령어가 주석에만 언급되고 실제로는 없었습니다.
watchdog이 잘못 만든 게 아니라 그냥 빠져 있던 것 — 여기서 새로 추가했습니다.
"""
import discord
from discord import app_commands
from discord.ext import commands, tasks
from thefuzz import process
from datetime import datetime, timedelta

from config import KST, ADMIN_ROLE_ID
from db import get_db
from utils import make_codeblock, schedule_ephemeral_delete, delete_message_after
from market_api import ITEM_LIST, get_market_price, fetch_market_sublist, refresh_item_dump_live
from data.item_data import DEKIA_DB, BLESS_DB, PEARL_OUTFIT_DB


async def build_dekia_ranking_embed() -> discord.Embed:
    res = [await get_market_price(it["id"], it["sid"]) for it in DEKIA_DB]
    rk = sorted(
        [{"n": it["name"], "l": it["light"], "p": p, "s": s, "u": p // it["light"]} for it, (p, s, _) in zip(DEKIA_DB, res) if p > 0],
        key=lambda x: x["u"],
    )
    d = "".join([f"**{idx+1}. {r['n']}**\n불빛: {r['l']} | 가격: {r['p']:,}\n**1개당: `{r['u']:,}`**\n\n" for idx, r in enumerate(rk)])
    return discord.Embed(title="데키아 랭킹", description=d[:4000] or "데이터 없음", color=0xf1c40f)


async def build_bless_ranking_embed() -> discord.Embed:
    res = [await get_market_price(it["id"]) for it in BLESS_DB]
    rk = sorted(
        [{"n": it["name"], "r": it["residue"], "p": p, "s": s, "u": p // it["residue"]} for it, (p, s, _) in zip(BLESS_DB, res) if p > 0],
        key=lambda x: x["u"],
    )
    d = "".join([f"**{idx+1}. {r['n']}**\n잔재: {r['r']} | 가격: {r['p']:,}\n**1개당: `{r['u']:,}`**\n\n" for idx, r in enumerate(rk)])
    return discord.Embed(title="영물 랭킹", description=d[:4000] or "데이터 없음", color=0x2ecc71)


class PearlTimeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def fs(self, interaction: discord.Interaction, hours: int, tl: str):
        await interaction.response.defer(ephemeral=True)
        conn = get_db()
        c = conn.cursor()
        now = datetime.now(KST)
        tt = (now - timedelta(hours=hours)).strftime('%Y-%m-%d %H:%M:%S')
        rk = []
        for i in PEARL_OUTFIT_DB:
            l = c.execute("SELECT total_trades, stock, timestamp FROM pearl_history WHERE item_id=? ORDER BY timestamp DESC LIMIT 1", (i['id'],)).fetchone()
            o = c.execute("SELECT total_trades FROM pearl_history WHERE item_id=? AND timestamp >= ? ORDER BY timestamp ASC LIMIT 1", (i['id'], tt)).fetchone()
            if l and o:
                rt = l[0] - o[0]
                est = f"{int((l[1]/rt)*hours)}시간" if rt > 0 else "계산 불가"
                rk.append({"name": i["name"], "trades": rt, "preorder": l[1], "last": l[2][5:16], "est": est})
        if not rk:
            msg = await interaction.followup.send("DB 수집 중입니다.", ephemeral=True, wait=True)
            interaction.client.loop.create_task(delete_message_after(msg, 60))
            return
        d = f"**{tl} 펄 의상 판매 개수**\n\n" + "".join(
            [f" {r['name']}\n거래수: {r['trades']} / 예약구매: {r['preorder']}\n예상: {r['est']}\n\n" for r in sorted(rk, key=lambda x: x["trades"], reverse=True)]
        )
        msg = await interaction.followup.send(embed=discord.Embed(color=0x9b59b6, description=d[:4000]), ephemeral=True, wait=True)
        interaction.client.loop.create_task(delete_message_after(msg, 60))

    @discord.ui.button(label="3일간")
    async def b3d(self, i: discord.Interaction, b: discord.ui.Button):
        await self.fs(i, 72, "3일간")

    @discord.ui.button(label="1일간")
    async def b1d(self, i: discord.Interaction, b: discord.ui.Button):
        await self.fs(i, 24, "1일간")

    @discord.ui.button(label="12시간")
    async def b12(self, i: discord.Interaction, b: discord.ui.Button):
        await self.fs(i, 12, "12시간")

    @discord.ui.button(label="6시간")
    async def b6(self, i: discord.Interaction, b: discord.ui.Button):
        await self.fs(i, 6, "6시간")


def _is_admin(interaction: discord.Interaction) -> bool:
    return bool(getattr(interaction.user.guild_permissions, 'administrator', False)) or bool(interaction.user.get_role(ADMIN_ROLE_ID))


class ItemLookupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pearl_tracker.start()
        self.daily_item_db_refresh.start()

    def cog_unload(self):
        self.pearl_tracker.cancel()
        self.daily_item_db_refresh.cancel()

    @tasks.loop(hours=24)
    async def daily_item_db_refresh(self):
        """arsha dump를 하루 1번 자동으로 갱신 시도. 실패해도 기존 ITEM_LIST는 그대로 유지됨."""
        print("🔄 아이템 DB 일일 자동 갱신 시작...")
        msg = await refresh_item_dump_live(force=True)
        print(f"🔄 아이템 DB 일일 자동 갱신 결과: {msg}")

    @daily_item_db_refresh.before_loop
    async def _before_daily_item_db_refresh(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=10)
    async def pearl_tracker(self):
        """펄 의상 거래 수 추적 — 10분마다 PEARL_OUTFIT_DB 아이템 거래소 데이터 수집."""
        conn = get_db()
        now_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
        for item in PEARL_OUTFIT_DB:
            try:
                entries = await fetch_market_sublist(item['id'])
                if not entries:
                    continue
                entry = entries[0]
                conn.execute(
                    "INSERT INTO pearl_history (item_id, timestamp, total_trades, stock) VALUES (?,?,?,?)",
                    (item['id'], now_str, entry.get('total_trades', 0), entry.get('current_stock', 0)),
                )
            except Exception:
                pass
        conn.commit()

    @pearl_tracker.before_loop
    async def _before_pearl_tracker(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="아이템디버그", description="[관리자] ITEM_LIST에 등록된 실제 이름/ID를 검색합니다")
    @app_commands.default_permissions(administrator=True)
    async def debug_item_list(self, interaction: discord.Interaction, 키워드: str):
        await interaction.response.defer(ephemeral=True)
        if not ITEM_LIST:
            await interaction.followup.send("ITEM_LIST가 아직 로드되지 않았습니다.", ephemeral=True)
            return

        key = 키워드.replace(" ", "")
        exact_hits = [(n, i) for n, i in ITEM_LIST.items() if key in n.replace(" ", "")]
        fuzzy_hits = process.extractBests(키워드, ITEM_LIST.keys(), limit=10)

        lines = [f"🔎 `{키워드}` 검색 결과 (ITEM_LIST 총 {len(ITEM_LIST)}개)\n"]
        lines.append(f"**부분일치: {len(exact_hits)}건**")
        for n, i in exact_hits[:15]:
            lines.append(f"  - `{n}` → id={i}")
        if not exact_hits:
            lines.append("  (없음)")

        lines.append("\n**유사도 매칭 (thefuzz, 컷오프 없음, 상위 10개)**")
        for n, score in fuzzy_hits:
            lines.append(f"  - `{n}` ({score}점) → id={ITEM_LIST[n]}")

        text = "\n".join(lines)
        if len(text) > 1900:
            text = text[:1900] + "\n...(생략)"
        await interaction.followup.send(make_codeblock(text), ephemeral=True)

    @app_commands.command(name="아이템디비갱신", description="[관리자] arsha dump를 새로 받아 아이템 DB를 강제 갱신합니다")
    @app_commands.default_permissions(administrator=True)
    async def refresh_item_db(self, interaction: discord.Interaction):
        if not _is_admin(interaction):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            schedule_ephemeral_delete(interaction)
            return
        await interaction.response.defer(ephemeral=True)
        msg = await refresh_item_dump_live(force=True)
        await interaction.followup.send(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ItemLookupCog(bot))
