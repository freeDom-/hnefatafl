/** Match Python's code-point limits without normalizing or truncating passwords. */
export function validatePasswordLength(value: string): void {
  if ([...value].length > 256) throw new Error('Use at most 256 characters for your password.');
}

export function validatePassword(value: string): void {
  const characters = [...value];
  if (characters.length < 12 || characters.length > 256 || new Set(characters).size < 4)
    throw new Error('Use 12–256 characters with at least four distinct characters.');
}
