	.text
	.globl	divide
	.type	divide, @function
divide:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	%edi, -4(%rbp)
	movl	%esi, -8(%rbp)
	movl	-4(%rbp), %eax
	cltd
	idivl	-8(%rbp)
	popq	%rbp
	ret
	.size	divide, .-divide
	.globl	modulo
	.type	modulo, @function
modulo:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	%edi, -4(%rbp)
	movl	%esi, -8(%rbp)
	movl	-4(%rbp), %eax
	cltd
	idivl	-8(%rbp)
	movl	%edx, %eax
	popq	%rbp
	ret
	.size	modulo, .-modulo
	.globl	main
	.type	main, @function
main:
	pushq	%rbp
	movq	%rsp, %rbp
	pushq	%rbx
	movl	$5, %esi
	movl	$-21, %edi
	call	divide
	movl	%eax, %ebx
	movl	$5, %esi
	movl	$-21, %edi
	call	modulo
	addl	%ebx, %eax
	movq	-8(%rbp), %rbx
	leave
	ret
	.size	main, .-main
