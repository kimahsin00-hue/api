"""
편의기능 패널 cog.

지금까지 옮긴 계산기/조회 cog들(calculators, sniper, dark_rift, item_lookup)의 View/Modal을
한 화면에 버튼으로 모아서 보여주는 패널입니다. 원본에서 UtilContainer/UtilView라는 이름의
Components V2(discord.ui.Container/LayoutView) 패널이었고, 계속 미뤄지다가 이번에
빠뜨린 걸 발견해서 마지막으로 옮깁니다.

DarkRiftActionView는 dark_rift cog를 옮기면서 cog 인스턴스를 받는 구조로 바뀌었기 때문에,
여기서는 bot.get_cog("DarkRiftCog")로 가져와서 넘겨줍니다.

주의: 이 cog는 다른 cog들이 이미 로드된 뒤에 로드되어야 합니다 (import 시점이 아니라
버튼 콜백 실행 시점에 필요한 것들을 가져오므로, main.py EXTENSIONS 순서는 크게 상관없지만
그래도 관례상 계산기/조회 cog들 뒤에 두는 걸 추천합니다).
"""
import discord
from discord import app_commands
from discord.ext import commands

from config import ADMIN_ROLE_ID
from db import save_panel
from utils import schedule_ephemeral_delete
from cogs.calculators import (
    CaphrasSelectView, ApDpModal, DevourSelectView, SovWeaponSelectView,
    TaxSelectView, AvgPriceModal,
)
from cogs.sniper import SnipeMainView
from cogs.dark_rift import DarkRiftActionView, get_current_rift_embed
from cogs.item_lookup import build_dekia_ranking_embed, build_bless_ranking_embed, PearlTimeView


def _is_admin(user: discord.Member) -> bool:
    return bool(getattr(user.guild_permissions, 'administrator', False)) or bool(user.get_role(ADMIN_ROLE_ID))


class UtilContainer(discord.ui.Container):
    def __init__(self, bot: commands.Bot, **kwargs):
        super().__init__(accent_color=0x2b2d31, **kwargs)
        self.bot = bot

        self.add_item(discord.ui.TextDisplay("**[편의기능]**"))
        self.add_item(discord.ui.TextDisplay("🗡️ 강화 및 공/방 계산"))
        row1 = discord.ui.ActionRow()
        row1.add_item(self._btn("카프라스 계산기", discord.ButtonStyle.success, "util_cap", self._cap))
        row1.add_item(self._btn("공/방 구간 계산기", discord.ButtonStyle.danger, "util_apdp", self._apdp))
        row1.add_item(self._btn("포식 계산기", discord.ButtonStyle.primary, "util_devour", self._devour))
        row1.add_item(self._btn("군왕 무기 계산기", discord.ButtonStyle.secondary, "util_sov", self._sov))
        self.add_item(row1)

        self.add_item(discord.ui.Separator())
        self.add_item(discord.ui.TextDisplay("💰 펄의상 및 거래소 효율 계산"))
        row2 = discord.ui.ActionRow()
        row2.add_item(self._btn("데키아 등불", discord.ButtonStyle.secondary, "util_dekia", self._dekia))
        row2.add_item(self._btn("영물의 축복", discord.ButtonStyle.secondary, "util_bless", self._bless))
        row2.add_item(self._btn("펄 의상", discord.ButtonStyle.secondary, "util_pearl", self._pearl))
        row2.add_item(self._btn("거래소 실수령액", discord.ButtonStyle.secondary, "util_tax", self._tax))
        row2.add_item(self._btn("아이템 평균가", discord.ButtonStyle.secondary, "util_avg", self._avg))
        self.add_item(row2)

        self.add_item(discord.ui.Separator())
        self.add_item(discord.ui.TextDisplay("📋 기타"))
        row3 = discord.ui.ActionRow()
        row3.add_item(self._btn("어둠의 틈", discord.ButtonStyle.secondary, "util_rift", self._rift))
        row3.add_item(self._btn("저격 수렵 계산기", discord.ButtonStyle.secondary, "util_snipe", self._snipe))
        self.add_item(row3)

    def _btn(self, label, style, cid, callback):
        btn = discord.ui.Button(label=label, style=style, custom_id=cid)
        btn.callback = callback
        return btn

    async def _cap(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=discord.Embed(title="카프라스 계산기", description="아래 버튼을 클릭해주세요.", color=0x2ecc71),
            view=CaphrasSelectView(), ephemeral=True,
        )
        schedule_ephemeral_delete(interaction)

    async def _apdp(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ApDpModal())

    async def _devour(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=discord.Embed(title="포식 계산기", description="아래 버튼을 클릭해주세요.", color=0x2b2d31),
            view=DevourSelectView(), ephemeral=True,
        )
        schedule_ephemeral_delete(interaction)

    async def _sov(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=discord.Embed(title="군왕 무기 계산기", description="부위를 선택해주세요.", color=0x2b2d31),
            view=SovWeaponSelectView(), ephemeral=True,
        )
        schedule_ephemeral_delete(interaction)

    async def _dekia(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        embed = await build_dekia_ranking_embed()
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _bless(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        embed = await build_bless_ranking_embed()
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _pearl(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=discord.Embed(title="펄 의상", color=0x2b2d31), view=PearlTimeView(), ephemeral=True)
        schedule_ephemeral_delete(interaction)

    async def _tax(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=discord.Embed(title="거래소 실수령액", color=0x2b2d31), view=TaxSelectView(), ephemeral=True)
        schedule_ephemeral_delete(interaction)

    async def _avg(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AvgPriceModal())

    async def _rift(self, interaction: discord.Interaction):
        embed = get_current_rift_embed()
        dark_rift_cog = self.bot.get_cog("DarkRiftCog")
        await interaction.response.send_message(embed=embed, view=DarkRiftActionView(dark_rift_cog), ephemeral=True)
        schedule_ephemeral_delete(interaction)

    async def _snipe(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=discord.Embed(title="저격 수렵", color=0x2b2d31), view=SnipeMainView(), ephemeral=True)
        schedule_ephemeral_delete(interaction)


class UtilView(discord.ui.LayoutView):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.add_item(UtilContainer(bot, id=1))


class UtilPanelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="설치-편의기능", description="[관리자] 편의기능 패널 설치 (Components V2)")
    @app_commands.default_permissions(administrator=True)
    async def cmd_install_util(self, interaction: discord.Interaction):
        if not _is_admin(interaction.user):
            await interaction.response.send_message("관리자만 사용할 수 있습니다.", ephemeral=True)
            schedule_ephemeral_delete(interaction)
            return
        msg = await interaction.channel.send(view=UtilView(self.bot))
        save_panel("util", msg)
        await interaction.response.send_message("✅ 편의기능 패널 설치 완료", ephemeral=True)
        schedule_ephemeral_delete(interaction)


async def setup(bot: commands.Bot):
    await bot.add_cog(UtilPanelCog(bot))
