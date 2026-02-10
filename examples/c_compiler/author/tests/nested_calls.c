#include <stdio.h>

int double_it(int x) {
    return x * 2;
}

int main() {
    printf("%d\n", double_it(double_it(3)));
    return 0;
}
