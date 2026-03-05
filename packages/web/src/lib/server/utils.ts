/**
 * Shared server-side utility helpers.
 */

/** Escape single quotes for SQL string literals. */
export function sqlLiteral(value: string): string {
  return value.replace(/'/g, "''");
}

/** Convert unknown thrown values into readable error text. */
export function formatError(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}
