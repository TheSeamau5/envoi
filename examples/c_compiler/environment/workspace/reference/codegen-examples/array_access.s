	.text
	.globl	main
	.type	main, @function
main:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	$1, -16(%rbp)
	movl	$2, -12(%rbp)
	movl	$3, -8(%rbp)
	movl	$4, -4(%rbp)
	movl	-8(%rbp), %eax
	popq	%rbp
	ret
	.size	main, .-main
