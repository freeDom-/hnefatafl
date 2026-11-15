import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { api, errorMessage } from './api';
import { useAuth } from './Auth';
import { AppearancePreferences } from './Appearance';
import { useI18n } from './i18n';
import { countryName } from './profileCountry';
import { profileText } from './profileText';
import { profileDate } from './profileDate';
import { validatePassword, validatePasswordLength } from './passwordPolicy';
import './profile.css';

export type Profile = {
  username: string;
  email: string;
  email_verified: boolean;
  display_name: string | null;
  country_code: string | null;
  city: string | null;
  date_of_birth: string | null;
  legacy_birth_year: number | null;
  legacy_private_fields: ('display_name' | 'country_code' | 'city')[];
  country_codes: string[];
  /** Effective server-owned portrait id; never a client-supplied URL. */
  portrait_id: string;
};
type Editable = Pick<Profile, 'display_name' | 'country_code' | 'city' | 'date_of_birth'>;
const legacyLabels = {
  display_name: 'Publish saved display name on save',
  country_code: 'Publish saved country on save',
  city: 'Publish saved city on save',
};
const fieldLabels = { display_name: 'Display name', country_code: 'Country', city: 'City' };

export default function ProfilePage() {
  const { account, observeVerification } = useAuth();
  const { t, locale } = useI18n();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [dirty, setDirty] = useState(new Set<keyof Editable>());
  const [loadError, setLoadError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const [changing, setChanging] = useState(false);
  const [sending, setSending] = useState(false);
  const [profileError, setProfileError] = useState('');
  const [passwordError, setPasswordError] = useState('');
  const [emailError, setEmailError] = useState('');
  const [saved, setSaved] = useState(false);
  const [passwordNotice, setPasswordNotice] = useState('');
  const [emailNotice, setEmailNotice] = useState('');
  const controller = useRef<AbortController | null>(null);
  const busy = useRef(new Set<string>());
  const today = new Date().toISOString().slice(0, 10);
  useEffect(() => {
    const request = new AbortController();
    controller.current = request;
    return () => request.abort();
  }, [account?.id]);
  useEffect(() => {
    if (!account) return;
    const request = new AbortController();
    const acceptVerification = observeVerification();
    setLoadError('');
    void api<Profile>('/profile', undefined, undefined, request.signal)
      .then((data) => {
        if (request.signal.aborted) return;
        acceptVerification(data.email_verified);
        setProfile(data);
        setDirty(new Set());
      })
      .catch((error) => {
        if (!request.signal.aborted) setLoadError(errorMessage(error));
      });
    return () => request.abort();
  }, [account?.id, attempt, observeVerification]);
  useEffect(() => {
    const focusSection = () => {
      if (location.hash === '#email-verification') {
        const section = document.getElementById('email-verification');
        section?.scrollIntoView?.({ block: 'start' });
        section?.focus({ preventScroll: true });
      }
    };
    focusSection();
    window.addEventListener('hashchange', focusSection);
    window.addEventListener('popstate', focusSection);
    return () => {
      window.removeEventListener('hashchange', focusSection);
      window.removeEventListener('popstate', focusSection);
    };
  }, []);
  const countries = useMemo(
    () =>
      (profile?.country_codes ?? [])
        .map((code) => ({ code, name: countryName(code, locale) }))
        .sort((a, b) => a.name.localeCompare(b.name, locale)),
    [profile?.country_codes, locale],
  );
  if (!account) return null;
  function edit(patch: Partial<Editable>) {
    setSaved(false);
    setDirty((current) => new Set([...current, ...(Object.keys(patch) as (keyof Editable)[])]));
    setProfile((current) => (current ? { ...current, ...patch } : current));
  }
  function legacyConfirmation(field: Profile['legacy_private_fields'][number]) {
    if (!profile?.legacy_private_fields.includes(field) || !profile[field]?.trim()) return null;
    const included = dirty.has(field);
    return (
      <button
        className="button secondary"
        type="button"
        aria-disabled={included}
        onClick={() => {
          if (!included) edit({ [field]: profile[field] });
        }}
      >
        <span aria-live="polite">
          {t(included ? '{field} will be public when saved.' : legacyLabels[field], {
            field: t(fieldLabels[field]),
          })}
        </span>
      </button>
    );
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!profile || busy.current.has('profile')) return;
    setProfileError('');
    setSaved(false);
    const changes: Partial<Editable> = {};
    try {
      for (const field of dirty) {
        changes[field] =
          field === 'display_name' || field === 'city'
            ? profileText(profile[field], field === 'display_name' ? 80 : 100)
            : field === 'date_of_birth'
              ? profileDate(profile[field])
              : profile[field];
      }
    } catch (error) {
      setProfileError(errorMessage(error));
      return;
    }
    if (!dirty.size) {
      setSaved(true);
      return;
    }
    busy.current.add('profile');
    setSaving(true);
    const acceptVerification = observeVerification();
    try {
      const updated = await api<Profile>('/profile', changes, 'PATCH', controller.current?.signal);
      acceptVerification(updated.email_verified);
      setProfile(updated);
      setDirty(new Set());
      setSaved(true);
      window.dispatchEvent(new Event('profile-changed'));
    } catch (error) {
      if (!controller.current?.signal.aborted) setProfileError(errorMessage(error));
    } finally {
      busy.current.delete('profile');
      setSaving(false);
    }
  }
  async function password(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy.current.has('password')) return;
    const form = event.currentTarget;
    const values = new FormData(form);
    const newPassword = String(values.get('new_password') ?? '');
    setPasswordError('');
    setPasswordNotice('');
    if (newPassword !== values.get('confirm_password')) {
      setPasswordError('Passwords do not match.');
      return;
    }
    try {
      validatePasswordLength(String(values.get('current_password') ?? ''));
      validatePassword(newPassword);
    } catch (error) {
      setPasswordError(errorMessage(error));
      return;
    }
    busy.current.add('password');
    setChanging(true);
    try {
      const result = await api<{ message: string }>(
        '/profile/password',
        {
          current_password: values.get('current_password'),
          new_password: newPassword,
          confirm_password: values.get('confirm_password'),
        },
        undefined,
        controller.current?.signal,
      );
      form.reset();
      setPasswordNotice(result.message);
    } catch (error) {
      if (!controller.current?.signal.aborted) setPasswordError(errorMessage(error));
    } finally {
      busy.current.delete('password');
      setChanging(false);
    }
  }
  async function resend() {
    if (busy.current.has('email')) return;
    busy.current.add('email');
    setSending(true);
    setEmailError('');
    setEmailNotice('');
    try {
      const result = await api<{ message: string }>(
        '/auth/verification/resend',
        { email: account!.email },
        undefined,
        controller.current?.signal,
      );
      setEmailNotice(result.message);
    } catch (error) {
      if (!controller.current?.signal.aborted) setEmailError(errorMessage(error));
    } finally {
      busy.current.delete('email');
      setSending(false);
    }
  }
  const verified = account.email_verified ?? profile?.email_verified;
  return (
    <section className="profile-page" aria-labelledby="profile-heading">
      <div className="page-intro">
        <h1 id="profile-heading" className="page-title" tabIndex={-1}>
          {t('My Profile')}
        </h1>
        <p>{t('Manage your account and preferences.')}</p>
      </div>
      <div className="profile-sections">
        <section className="panel profile-section" aria-labelledby="personal-info-heading">
          <h2 id="personal-info-heading">{t('Personal Information')}</h2>
          <p className="form-help">
            {t('All fields are optional. Your login username stays {username}.', {
              username: account.username,
            })}
          </p>
          {!profile && !loadError && <p role="status">{t('Loading your profile…')}</p>}
          {loadError && (
            <p role="alert">
              {t(loadError)}{' '}
              <button
                type="button"
                className="button secondary"
                onClick={() => setAttempt((value) => value + 1)}
              >
                {t('Retry')}
              </button>
            </p>
          )}
          {profile && (
            <form
              className="form-section"
              onSubmit={(event) => void save(event)}
              aria-busy={saving}
            >
              <fieldset className="form-section" disabled={saving}>
                <label>
                  {t('Display name')}
                  <input
                    name="display_name"
                    value={profile.display_name ?? ''}
                    autoComplete="nickname"
                    onChange={(event) => edit({ display_name: event.target.value })}
                  />
                </label>
                {legacyConfirmation('display_name')}
                <label>
                  {t('Country')}
                  <select
                    value={profile.country_code ?? ''}
                    onChange={(event) => edit({ country_code: event.target.value || null })}
                  >
                    <option value="">{t('Not specified')}</option>
                    {countries.map(({ code, name }) => (
                      <option key={code} value={code}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
                {legacyConfirmation('country_code')}
                <label>
                  {t('City')}
                  <input
                    name="city"
                    value={profile.city ?? ''}
                    autoComplete="address-level2"
                    onChange={(event) => edit({ city: event.target.value })}
                  />
                </label>
                {legacyConfirmation('city')}
                <label>
                  {t('Date of birth')}
                  <input
                    name="date_of_birth"
                    type="date"
                    autoComplete="bday"
                    min="1900-01-01"
                    max={today}
                    value={profile.date_of_birth ?? ''}
                    onChange={(event) => edit({ date_of_birth: event.target.value || null })}
                  />
                </label>
                {profile.legacy_birth_year != null && !profile.date_of_birth && (
                  <p className="form-help">
                    {t(
                      'Previously saved birth year: {year}. Enter a full date to publish your age.',
                      { year: profile.legacy_birth_year },
                    )}
                  </p>
                )}
              </fieldset>
              {profileError && (
                <p className="form-error" role="alert">
                  {t(profileError)}
                </p>
              )}
              {saved && <p role="status">{t('Profile saved.')}</p>}
              <button className="button primary" disabled={saving}>
                {t(saving ? 'Saving…' : 'Save Profile')}
              </button>
            </form>
          )}
        </section>
        <section className="panel profile-section" aria-labelledby="account-security-heading">
          <h2 id="account-security-heading">{t('Account Security')}</h2>
          <section
            id="email-verification"
            tabIndex={-1}
            aria-labelledby="email-verification-heading"
          >
            <h3 id="email-verification-heading">{t('Email verification')}</h3>
            <p className="profile-email">{account.email}</p>
            <p>{t(verified ? 'Verified' : 'Not verified')}</p>
            {!verified && (
              <button
                className="button secondary"
                type="button"
                disabled={sending}
                onClick={() => void resend()}
              >
                {t(sending ? 'Sending…' : 'Send Verification Email')}
              </button>
            )}
            {emailError && (
              <p className="form-error" role="alert">
                {t(emailError)}
              </p>
            )}
            {emailNotice && <p role="status">{t(emailNotice)}</p>}
          </section>
          <form
            className="form-section"
            onSubmit={(event) => void password(event)}
            aria-busy={changing}
          >
            <h3>{t('Change Password')}</h3>
            <fieldset className="form-section" disabled={changing}>
              <label>
                {t('Current password')}
                <input
                  name="current_password"
                  type="password"
                  autoComplete="current-password"
                  required
                />
              </label>
              <label>
                {t('New password')}
                <input
                  name="new_password"
                  type="password"
                  autoComplete="new-password"
                  required
                  aria-describedby="profile-password-policy"
                />
              </label>
              <p id="profile-password-policy" className="form-help">
                {t('Use 12–256 characters with at least four distinct characters.')}
              </p>
              <label>
                {t('Confirm new password')}
                <input
                  name="confirm_password"
                  type="password"
                  autoComplete="new-password"
                  required
                />
              </label>
            </fieldset>
            {passwordError && (
              <p className="form-error" role="alert">
                {t(passwordError)}
              </p>
            )}
            {passwordNotice && <p role="status">{t(passwordNotice)}</p>}
            <button className="button primary" disabled={changing}>
              {t(changing ? 'Changing…' : 'Change Password')}
            </button>
          </form>
        </section>
        <section
          className="panel profile-section profile-appearance"
          aria-labelledby="profile-appearance-heading"
        >
          <h2 id="profile-appearance-heading">{t('Appearance')}</h2>
          <AppearancePreferences />
        </section>
      </div>
    </section>
  );
}
