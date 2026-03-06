struct Pair {
    int left;
    int right;
};

int main(void) {
    struct Pair pair = {10, 20};
    return pair.left + pair.right;
}
