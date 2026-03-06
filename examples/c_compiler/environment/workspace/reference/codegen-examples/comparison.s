	.text
	.globl	lt
	.type	lt, @function
lt:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	%edi, -4(%rbp)
	movl	%esi, -8(%rbp)
	movl	-4(%rbp), %eax
	cmpl	-8(%rbp), %eax
	setl	%al
	movzbl	%al, %eax
	popq	%rbp
	ret
	.size	lt, .-lt
	.globl	main
	.type	main, @function
main:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	$4, %esi
	movl	$3, %edi
	call	lt
	popq	%rbp
	ret
	.size	main, .-main
