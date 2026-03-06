#include <stdio.h>

int double_up(int value);
int triple_up(int value);

int main(void) {
    printf("%d\n", double_up(2) + triple_up(4));
    return 0;
}
