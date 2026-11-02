import React from 'react';
import { AnalyticalListPage } from './components/AnalyticalListPage';

export const App: React.FC = () => {
  return (
    <AnalyticalListPage
      apiUrl={import.meta.env.VITE_API_URL || 'http://localhost:8000'}
      tenantId={import.meta.env.VITE_TENANT_ID || 'shree_shashwatraj_agro'}
      token={import.meta.env.VITE_AUTH_TOKEN}
    />
  );
};

export default App;
