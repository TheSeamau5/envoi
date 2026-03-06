static int hidden_fn(void) {
    return 2;
}

int public_fn(void) {
    return hidden_fn();
}
