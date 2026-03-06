int public_fn(void);
int hidden_fn(void);

int main(void) {
    return public_fn() + hidden_fn();
}
