import React, { useEffect, useState } from 'react';
import Discovery from './components/Discovery';
import telegram from './utils/telegram';
import './App.css';

function App() {
  const [isLoading, setIsLoading] = useState(true);
  const [user, setUser] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    initializeApp();
  }, []);

  const initializeApp = async () => {
    try {
      // Initialize Telegram WebApp
      const webApp = telegram.init();
      
      if (!webApp) {
        throw new Error('Not running in Telegram WebApp environment');
      }

      // Get Telegram user data
      const telegramUser = telegram.getUser();
      
      if (!telegramUser) {
        throw new Error('Failed to get user data from Telegram');
      }

      setUser(telegramUser);
      
      // Validate authentication with backend
      const initData = webApp.initData;
      await telegram.validateAuth(initData);
      
      setIsLoading(false);
      
      // Notify Telegram that app is ready
      webApp.ready();
      
    } catch (err) {
      console.error('App initialization failed:', err);
      setError(err.message);
      setIsLoading(false);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900">
        <div className="text-center">
          {/* Glassmorphic loader */}
          <div className="backdrop-blur-md bg-white/10 border border-white/20 rounded-3xl p-8 shadow-glass">
            <div className="w-16 h-16 mx-auto mb-4 border-4 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin"></div>
            <p className="text-white text-lg font-medium">
              Loading Premium Dating...
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 px-4">
        <div className="backdrop-blur-md bg-white/10 border border-white/20 rounded-3xl p-8 shadow-glass text-center max-w-md">
          <svg className="w-16 h-16 mx-auto mb-4 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
          <h2 className="text-white text-xl font-bold mb-2">
            Initialization Error
          </h2>
          <p className="text-white/80 mb-4">
            {error}
          </p>
          <p className="text-white/60 text-sm">
            Please open this app through Telegram
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="App">
      <Discovery />
    </div>
  );
}

export default App;
