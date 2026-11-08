type PublicAccount = { name: string; username?: string };

/** The username is public and canonical; shared display names are not unique. */
export function accountAttribution(account: PublicAccount | null | undefined): string {
  return account?.username && account.username !== account.name ? `(@${account.username})` : '';
}

export function accountName(account: PublicAccount | null | undefined): string | undefined {
  if (!account) return undefined;
  return [account.name, accountAttribution(account)].filter(Boolean).join(' ');
}
