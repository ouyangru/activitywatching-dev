"""Shared activity vocabulary. Observation state remains separate from meaning."""

OFFLINE_CATEGORIES = ("睡眠", "运动", "出游", "用餐", "通勤", "休息", "家务")
CATEGORIES = ("学习", "工作", "娱乐", "空闲", "其他", *OFFLINE_CATEGORIES)
NO_DEVICE_CATEGORY = "无设备记录"

