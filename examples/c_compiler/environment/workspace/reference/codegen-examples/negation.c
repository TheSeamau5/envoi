int negate(int x) {
    return -x;
}

int bitwise_not(int x) {
    return ~x;
}

int logical_not(int x) {
    return !x;
}

int main(void) {
    return negate(5) + bitwise_not(1) + logical_not(0);
}
