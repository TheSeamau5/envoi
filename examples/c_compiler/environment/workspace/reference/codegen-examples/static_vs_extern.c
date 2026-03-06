static int internal_counter = 3;
int external_counter = 4;

static int internal(void) {
    return internal_counter;
}

int external(void) {
    return internal() + external_counter;
}

int main(void) {
    return external();
}
