int triple_up(int value);

int double_up(int value) {
    return value * 2 + triple_up(1);
}
