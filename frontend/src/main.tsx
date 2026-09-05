import React from 'react';
import ReactDOM from 'react-dom/client';
import { AppProviders } from './app/providers';
import { AppRouter } from './app/router';
import './styles/globals.css';
import { USE_MOCKS } from './lib/constants';

async function prepareApp() {
  if (USE_MOCKS || import.meta.env.DEV) {
    try {
      const { worker } = await import('./mocks/browser');
      await worker.start({
        onUnhandledRequest: 'bypass',
      });
    } catch (e) {
      console.warn('Failed to start MSW worker', e);
    }
  }
}

prepareApp().then(() => {
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <AppProviders>
        <AppRouter />
      </AppProviders>
    </React.StrictMode>
  );
});
