def branchy(value: int) -> int:
    if value == 0:
        value += 1
    else:
        value -= 1
    if value == 1:
        value += 1
    else:
        value -= 1
    if value == 2:
        value += 1
    else:
        value -= 1
    if value == 3:
        value += 1
    else:
        value -= 1
    if value == 4:
        value += 1
    else:
        value -= 1
    if value == 5:
        value += 1
    else:
        value -= 1
    if value == 99:
        value += 1
    return value
