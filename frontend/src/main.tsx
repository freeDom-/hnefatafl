import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { AppearanceProvider } from './Appearance';
import './style.css';
import './appearance.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppearanceProvider>
      <App />
    </AppearanceProvider>
  </React.StrictMode>,
);
