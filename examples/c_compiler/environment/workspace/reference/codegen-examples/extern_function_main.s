	.text
	.globl	main
	.type	main, @function
main:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	$9, %esi
	movl	$8, %edi
	call	add
	popq	%rbp
	ret
	.size	main, .-main
