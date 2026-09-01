from vkbottle import Keyboard, KeyboardButtonColor, Text


def main_menu(is_registered: bool = False, is_admin: bool = False):
    kb = Keyboard(one_time=False)

    if not is_registered:
        kb.add(Text("Регистрация"), color=KeyboardButtonColor.POSITIVE)
    else:
        kb.add(Text("Личный кабинет"), color=KeyboardButtonColor.PRIMARY)
        kb.add(Text("Мой профиль"), color=KeyboardButtonColor.SECONDARY)

    kb.row()
    kb.add(Text("Информация"), color=KeyboardButtonColor.SECONDARY)

    if is_admin:
        kb.row()
        kb.add(Text("Админ-панель"), color=KeyboardButtonColor.NEGATIVE)

    return kb


def gender_keyboard():
    return (
        Keyboard(one_time=True)
        .add(Text("Мужской"), color=KeyboardButtonColor.PRIMARY)
        .add(Text("Женский"), color=KeyboardButtonColor.POSITIVE)
        .row()
        .add(Text("Отмена"), color=KeyboardButtonColor.NEGATIVE)
    )


def cancel_keyboard():
    return Keyboard(one_time=True).add(
        Text("Отмена"), color=KeyboardButtonColor.NEGATIVE
    )


def admin_keyboard():
    return (
        Keyboard(one_time=False)
        .add(Text("Заявки"), color=KeyboardButtonColor.PRIMARY)
        .add(Text("Игроки"), color=KeyboardButtonColor.SECONDARY)
        .row()
        .add(Text("Назад"), color=KeyboardButtonColor.NEGATIVE)
    )


def approve_keyboard(user_id: int):
    return (
        Keyboard(inline=True)
        .add(
            Text("Одобрить", payload={"cmd": "approve", "user_id": user_id}),
            color=KeyboardButtonColor.POSITIVE,
        )
        .add(
            Text("Отклонить", payload={"cmd": "reject", "user_id": user_id}),
            color=KeyboardButtonColor.NEGATIVE,
        )
    )
