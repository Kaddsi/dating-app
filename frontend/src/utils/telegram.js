/**
 * Premium Dating TWA - Telegram WebApp Integration
 * Handles authentication, theme sync, and Telegram API interactions
 */

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

// Initialize Telegram WebApp
export const initTelegramWebApp = () => {
  const webApp = window.Telegram?.WebApp;
  
  if (!webApp) {
    console.error('Telegram WebApp is not available');
    return null;
  }
  
  // Expand to full height
  webApp.expand();
  
  // Enable closing confirmation
  webApp.enableClosingConfirmation();
  
  // Set header color to match our dark theme
  webApp.setHeaderColor('#0f172a'); // slate-900
  
  // Set background color
  webApp.setBackgroundColor('#1e293b'); // slate-800
  
  return webApp;
};


// Validate Telegram init data on backend
export const validateTelegramAuth = async (initData) => {
  try {
    const response = await fetch(`${API_BASE_URL}/api/auth/validate`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ initData })
    });
    
    if (!response.ok) {
      throw new Error('Authentication failed');
    }
    
    const data = await response.json();
    return data;
  } catch (error) {
    console.error('Telegram auth validation failed:', error);
    throw error;
  }
};


// Parse Telegram WebApp init data
export const parseTelegramInitData = (initData) => {
  const params = new URLSearchParams(initData);
  const data = {};
  
  for (const [key, value] of params.entries()) {
    try {
      // Try to parse JSON values
      data[key] = JSON.parse(value);
    } catch {
      // Keep as string if not JSON
      data[key] = value;
    }
  }
  
  return data;
};


// Get user info from Telegram
export const getTelegramUser = () => {
  const webApp = window.Telegram?.WebApp;
  
  if (!webApp?.initDataUnsafe?.user) {
    return null;
  }
  
  return {
    id: webApp.initDataUnsafe.user.id,
    firstName: webApp.initDataUnsafe.user.first_name,
    lastName: webApp.initDataUnsafe.user.last_name,
    username: webApp.initDataUnsafe.user.username,
    languageCode: webApp.initDataUnsafe.user.language_code,
    isPremium: webApp.initDataUnsafe.user.is_premium,
    photoUrl: webApp.initDataUnsafe.user.photo_url
  };
};


// Haptic feedback helpers
export const haptic = {
  light: () => window.Telegram?.WebApp?.HapticFeedback?.impactOccurred('light'),
  medium: () => window.Telegram?.WebApp?.HapticFeedback?.impactOccurred('medium'),
  heavy: () => window.Telegram?.WebApp?.HapticFeedback?.impactOccurred('heavy'),
  success: () => window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('success'),
  warning: () => window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('warning'),
  error: () => window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred('error'),
};


// Show main button (for actions)
export const showMainButton = (text, onClick) => {
  const webApp = window.Telegram?.WebApp;
  
  if (!webApp?.MainButton) return;
  
  webApp.MainButton.setText(text);
  webApp.MainButton.show();
  webApp.MainButton.onClick(onClick);
};


// Hide main button
export const hideMainButton = () => {
  const webApp = window.Telegram?.WebApp;
  webApp?.MainButton?.hide();
};


// Open Telegram link
export const openTelegramLink = (url) => {
  window.Telegram?.WebApp?.openTelegramLink(url);
};


// Open external link
export const openLink = (url, options = {}) => {
  window.Telegram?.WebApp?.openLink(url, options);
};


// Send data back to bot
export const sendDataToBot = (data) => {
  window.Telegram?.WebApp?.sendData(JSON.stringify(data));
};


// Close WebApp
export const closeWebApp = () => {
  window.Telegram?.WebApp?.close();
};


// Check if running in Telegram
export const isTelegramWebApp = () => {
  return Boolean(window.Telegram?.WebApp);
};


// Get theme params
export const getThemeParams = () => {
  return window.Telegram?.WebApp?.themeParams || {};
};


// Request write access (for sending messages)
export const requestWriteAccess = async () => {
  return new Promise((resolve) => {
    window.Telegram?.WebApp?.requestWriteAccess((granted) => {
      resolve(granted);
    });
  });
};


// Request contact (alternative to bot keyboard)
export const requestContact = async () => {
  return new Promise((resolve) => {
    window.Telegram?.WebApp?.requestContact((granted, contact) => {
      resolve({ granted, contact });
    });
  });
};


// Show popup
export const showPopup = (params) => {
  return new Promise((resolve) => {
    window.Telegram?.WebApp?.showPopup(params, (buttonId) => {
      resolve(buttonId);
    });
  });
};


// Show alert
export const showAlert = (message) => {
  return new Promise((resolve) => {
    window.Telegram?.WebApp?.showAlert(message, () => {
      resolve();
    });
  });
};


// Show confirm
export const showConfirm = (message) => {
  return new Promise((resolve) => {
    window.Telegram?.WebApp?.showConfirm(message, (confirmed) => {
      resolve(confirmed);
    });
  });
};


// Scan QR code
export const scanQRCode = (text = 'Scan QR Code') => {
  return new Promise((resolve, reject) => {
    window.Telegram?.WebApp?.showScanQrPopup({ text }, (data) => {
      if (data) {
        resolve(data);
      } else {
        reject(new Error('QR scan cancelled'));
      }
    });
  });
};


// Cloud storage helpers (for saving user preferences)
export const cloudStorage = {
  setItem: (key, value) => {
    return new Promise((resolve, reject) => {
      window.Telegram?.WebApp?.CloudStorage?.setItem(key, value, (error, success) => {
        if (error) reject(error);
        else resolve(success);
      });
    });
  },
  
  getItem: (key) => {
    return new Promise((resolve, reject) => {
      window.Telegram?.WebApp?.CloudStorage?.getItem(key, (error, value) => {
        if (error) reject(error);
        else resolve(value);
      });
    });
  },
  
  getItems: (keys) => {
    return new Promise((resolve, reject) => {
      window.Telegram?.WebApp?.CloudStorage?.getItems(keys, (error, values) => {
        if (error) reject(error);
        else resolve(values);
      });
    });
  },
  
  removeItem: (key) => {
    return new Promise((resolve, reject) => {
      window.Telegram?.WebApp?.CloudStorage?.removeItem(key, (error, success) => {
        if (error) reject(error);
        else resolve(success);
      });
    });
  },
  
  removeItems: (keys) => {
    return new Promise((resolve, reject) => {
      window.Telegram?.WebApp?.CloudStorage?.removeItems(keys, (error, success) => {
        if (error) reject(error);
        else resolve(success);
      });
    });
  },
  
  getKeys: () => {
    return new Promise((resolve, reject) => {
      window.Telegram?.WebApp?.CloudStorage?.getKeys((error, keys) => {
        if (error) reject(error);
        else resolve(keys);
      });
    });
  }
};

const telegram = {
  init: initTelegramWebApp,
  validateAuth: validateTelegramAuth,
  getUser: getTelegramUser,
  haptic,
  showAlert,
  showConfirm,
  showPopup,
  close: closeWebApp,
  isTelegramWebApp,
};

export default telegram;


export default {
  init: initTelegramWebApp,
  validateAuth: validateTelegramAuth,
  parseInitData: parseTelegramInitData,
  getUser: getTelegramUser,
  haptic,
  showMainButton,
  hideMainButton,
  openTelegramLink,
  openLink,
  sendDataToBot,
  closeWebApp,
  isTelegramWebApp,
  getThemeParams,
  requestWriteAccess,
  requestContact,
  showPopup,
  showAlert,
  showConfirm,
  scanQRCode,
  cloudStorage
};
