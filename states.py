from vkbottle import BaseStateGroup, BuiltinStateDispenser

state_dispenser = BuiltinStateDispenser()


class RegState(BaseStateGroup):
    NICKNAME = 1
    AGE = 2
    GENDER = 3
    CITY = 4
    ABOUT = 5
