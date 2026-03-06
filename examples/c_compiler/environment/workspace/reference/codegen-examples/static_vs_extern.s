	.text
	.data
	.align 4
	.type	internal_counter, @object
	.size	internal_counter, 4
internal_counter:
	.long	3
	.globl	external_counter
	.align 4
	.type	external_counter, @object
	.size	external_counter, 4
external_counter:
	.long	4
	.text
	.type	internal, @function
internal:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	internal_counter(%rip), %eax
	popq	%rbp
	ret
	.size	internal, .-internal
	.globl	external
	.type	external, @function
external:
	pushq	%rbp
	movq	%rsp, %rbp
	call	internal
	movl	external_counter(%rip), %edx
	addl	%edx, %eax
	popq	%rbp
	ret
	.size	external, .-external
	.globl	main
	.type	main, @function
main:
	pushq	%rbp
	movq	%rsp, %rbp
	call	external
	popq	%rbp
	ret
	.size	main, .-main
