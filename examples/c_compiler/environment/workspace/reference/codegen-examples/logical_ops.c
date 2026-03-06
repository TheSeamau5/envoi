int decide(int a, int b, int c) {
    return (a && b) || c;
}

int main(void) {
    return decide(1, 0, 7);
}
