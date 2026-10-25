import React from 'react';
import ReactDOM from 'react-dom/client';
import PlaySurfaces from './PlaySurfaces';
import { AppearanceProvider } from './Appearance';
import { I18nProvider } from './i18n';
import './style.css';
import './appearance.css';
import './identity.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <I18nProvider>
      <AppearanceProvider>
        <PlaySurfaces />
      </AppearanceProvider>
    </I18nProvider>
  </React.StrictMode>,
);
