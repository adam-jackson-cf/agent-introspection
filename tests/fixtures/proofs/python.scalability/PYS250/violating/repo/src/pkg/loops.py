def scan(a: list[list[list[int]]]) -> int:
    total = 0
    for x in a:
        for y in x:
            for z in y:
                total += z
    return total
