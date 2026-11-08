import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { api, errorMessage } from './api';
import { useAuth } from './Auth';
import { AppearancePreferences } from './Appearance';
import { useI18n } from './i18n';
import { countryName } from './profileCountry';
import { profileText } from './profileText';
import './profile.css';

export type Profile = {
  username: string;
  email: string;
  email_verified: boolean;
  display_name: string | null;
  country_code: string | null;
  city: string | null;
  birth_year: number | null;
  show_display_name: boolean;
  show_country: boolean;
  show_city: boolean;
  show_age: boolean;
  country_codes: string[];
};
const visibility = [
  ['show_display_name', 'Share display name'],
  ['show_country', 'Share country'],
  ['show_city', 'Share city'],
  ['show_age', 'Share age range'],
] as const;

export default function ProfilePage() {
  const { account, observeVerification } = useAuth();
  const { t, locale } = useI18n();
  const [profile, setProfile] = useState<Profile | null>(null);
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
  const year = new Date().getUTCFullYear();
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
  function edit(patch: Partial<Profile>) {
    setSaved(false);
    setProfile((current) => (current ? { ...current, ...patch } : current));
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!profile || busy.current.has('profile')) return;
    setProfileError('');
    setSaved(false);
    let display_name: string | null;
    let city: string | null;
    try {
      display_name = profileText(profile.display_name, 80);
      city = profileText(profile.city, 100);
    } catch (error) {
      setProfileError(errorMessage(error));
      return;
    }
    if (
      profile.birth_year !== null &&
      (!Number.isInteger(profile.birth_year) ||
        profile.birth_year < 1900 ||
        profile.birth_year > year)
    ) {
      setProfileError('Enter a year from 1900 to {year}.');
      return;
    }
    busy.current.add('profile');
    setSaving(true);
    const acceptVerification = observeVerification();
    try {
      const updated = await api<Profile>(
        '/profile',
        {
          display_name,
          city,
          country_code: profile.country_code,
          birth_year: profile.birth_year,
          ...Object.fromEntries(visibility.map(([key]) => [key, profile[key]])),
        },
        'PATCH',
        controller.current?.signal,
      );
      acceptVerification(updated.email_verified);
      setProfile(updated);
      setSaved(true);
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
    if (
      [...newPassword].length < 12 ||
      [...newPassword].length > 256 ||
      new Set(newPassword).size < 4
    ) {
      setPasswordError('Use 12–256 characters with at least four distinct characters.');
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
                    maxLength={80}
                    autoComplete="nickname"
                    onChange={(event) => edit({ display_name: event.target.value })}
                  />
                </label>
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
                <label>
                  {t('City')}
                  <input
                    name="city"
                    value={profile.city ?? ''}
                    maxLength={100}
                    autoComplete="address-level2"
                    onChange={(event) => edit({ city: event.target.value })}
                  />
                </label>
                <label>
                  {t('Year of birth')}
                  <input
                    name="birth_year"
                    type="number"
                    min={1900}
                    max={year}
                    step={1}
                    value={profile.birth_year ?? ''}
                    onChange={(event) =>
                      edit({
                        birth_year: event.target.value === '' ? null : Number(event.target.value),
                      })
                    }
                  />
                </label>
                <fieldset className="form-section" aria-describedby="profile-privacy-help">
                  <legend>{t('Leaderboard visibility')}</legend>
                  <p id="profile-privacy-help" className="form-help">
                    {t(
                      'Only checked information is visible to other players. Your username is used unless you share a display name. Shared names also appear in game views. Your birth year stays private; sharing age shows a broad bracket of roughly ten years, allowing for the unknown birthday. Very young players’ ages may remain hidden to protect their birth year.',
                    )}
                  </p>
                  {visibility.map(([key, label]) => (
                    <label className="checkbox-label" key={key}>
                      <input
                        type="checkbox"
                        checked={profile[key]}
                        onChange={(event) => edit({ [key]: event.target.checked })}
                      />
                      {t(label)}
                    </label>
                  ))}
                </fieldset>
              </fieldset>
              {profileError && (
                <p className="form-error" role="alert">
                  {t(profileError, { year })}
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
                  maxLength={256}
                />
              </label>
              <label>
                {t('New password')}
                <input
                  name="new_password"
                  type="password"
                  autoComplete="new-password"
                  required
                  minLength={12}
                  maxLength={256}
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
                  maxLength={256}
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
