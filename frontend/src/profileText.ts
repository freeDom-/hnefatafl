/** Match the backend's narrow control policy; preserve international text and emoji joiners. */
const controls = /[\p{Cc}\p{Bidi_Control}\u206a-\u206f]/u;
export function profileText(value: string | null, maximum: number): string | null {
  const normalized = (value ?? '').normalize('NFC');
  if (controls.test(normalized))
    throw new Error(
      'Names and cities must not contain control characters or text direction controls.',
    );
  const trimmed = normalized.trim();
  if ([...trimmed].length > maximum)
    throw new Error(
      'Use up to 80 characters for your display name and 100 for your city, without control characters.',
    );
  return trimmed || null;
}
