#include <stdio.h>

extern int counter;
void bump(void);

int main(void) {
    bump();
    bump();
    printf("%d\n", counter);
    return 0;
}
