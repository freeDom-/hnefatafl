import React from 'react';
import ReactDOM from 'react-dom/client';
import EntryRouter from './EntryRouter';
import { AppearanceProvider } from './Appearance';
import { I18nProvider } from './i18n';
import { NotificationProvider } from './notifications';
import './style.css';
import './appearance.css';
import './arena-themes.css';
import './arenaSidebar.css';
import './identity.css';
import './ui.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <I18nProvider>
      <NotificationProvider>
        <AppearanceProvider>
          <EntryRouter />
        </AppearanceProvider>
      </NotificationProvider>
    </I18nProvider>
  </React.StrictMode>,
);
