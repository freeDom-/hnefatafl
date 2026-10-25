import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import Auth from './Auth';
import { AppearanceProvider } from './Appearance';
import { I18nProvider } from './i18n';
import { NotificationProvider } from './notifications';
import './style.css';
import './appearance.css';
import './identity.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <I18nProvider>
      <NotificationProvider>
        <AppearanceProvider>
          <Auth>
            <App />
          </Auth>
        </AppearanceProvider>
      </NotificationProvider>
    </I18nProvider>
  </React.StrictMode>,
);
