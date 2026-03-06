	.text
	.globl	main
	.type	main, @function
main:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	$14, %eax
	popq	%rbp
	ret
	.size	main, .-main
