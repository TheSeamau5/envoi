// expect_stdout: 6
#include <stdio.h>

int main() {
    int sum = 0;
    int i = 1;
    while (i <= 3) {
        int j = 1;
        while (j <= i) {
            sum = sum + 1;
            j = j + 1;
        }
        i = i + 1;
    }
    printf("%d\n", sum);
    return 0;
}
