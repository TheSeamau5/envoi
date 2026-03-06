#include <stdio.h>

extern int add(int a, int b);

int main(void) {
    printf("%d\n", add(3, 4));
    return 0;
}
