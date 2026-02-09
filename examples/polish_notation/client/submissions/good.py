import sys


def evaluate(tokens):
    token = tokens.pop(0)
    if token in ("+", "-", "*"):
        a = evaluate(tokens)
        b = evaluate(tokens)
        if token == "+":
            return a + b
        if token == "-":
            return a - b
        if token == "*":
            return a * b
    return int(token)


print(evaluate(sys.argv[1].split()))
