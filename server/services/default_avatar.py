"""内置默认用户头像。"""
from __future__ import annotations

import secrets


DEFAULT_AVATAR_FILES = (
    "0f172b46e5536515-cassowary-2.png",
    "2239f6fd9bdc1643-whale-4.png",
    "2e99eb62f01d03de-fennec-fox-2.png",
    "388091f222839862-tapir-3.png",
    "3e4a48ff9042c859-red-panda-2.png",
    "4d6ecf91d12c3ba5-dumpling.png",
    "4ddf84d5a5ae7119-rice-cooker-bot.png",
    "51cf9d38cb29760f-manatee-2.png",
    "6294f85e7756edb6-anglerfish.png",
    "6564e8db39aba32f-axolotl-3.png",
    "88deefb7e2bed04a-flamingo-3.png",
    "9dc2e151a484833f-kettle-bot.png",
    "a746787047a05c50-quokka-2.png",
    "b28208770b025ce5-hippo.png",
    "c2cd710ee22b6692-lotus-robot.png",
    "d1a535cea90bd6ff-robot-4.png",
    "d5d8d72aec39d995-book-spirit.png",
    "df85b238e7350e8f-alpaca-2.png",
    "e09ffd9919eacc74-moon-moth.png",
    "e3cf643c3aed070d-koi-3.png",
    "ee2828cd95f2cc32-pangolin-3.png",
    "f767af872c3519c0-crab.png",
)


def get_default_avatar() -> str:
    """返回项目内置的随机默认头像 URL。"""
    return f"/avatars/ipaslogo/{secrets.choice(DEFAULT_AVATAR_FILES)}"
