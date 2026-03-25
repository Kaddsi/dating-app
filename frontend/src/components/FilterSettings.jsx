/**
 * Premium Dating TWA - Настройки фильтров
 * Позволяет пользователям настроить предпочтения поиска
 */

import React, { useState, useEffect } from 'react';
import telegram from '../utils/telegram';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const FilterSettings = () => {
  const [filters, setFilters] = useState({
    looking_for: 'everyone',  // male, female, everyone
    age_min: 18,
    age_max: 35,
    max_distance: 50,  // км
    required_interests: []
  });
  
  const [loading, setLoading] = useState(false);
  
  // Загрузить текущие настройки
  useEffect(() => {
    fetchCurrentFilters();
  }, []);
  
  const fetchCurrentFilters = async () => {
    try {
      const webApp = window.Telegram?.WebApp;
      const initData = webApp?.initData;
      
      const response = await fetch(`${API_BASE_URL}/api/user/filters`, {
        headers: {
          'Authorization': `tma ${initData}`
        }
      });
      
      const data = await response.json();
      setFilters(data.filters);
    } catch (error) {
      console.error('Ошибка загрузки фильтров:', error);
    }
  };
  
  const saveFilters = async () => {
    setLoading(true);
    telegram.haptic.medium();
    
    try {
      const webApp = window.Telegram?.WebApp;
      const initData = webApp?.initData;
      
      const response = await fetch(`${API_BASE_URL}/api/user/filters`, {
        method: 'PUT',
        headers: {
          'Authorization': `tma ${initData}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(filters)
      });
      
      if (response.ok) {
        telegram.haptic.success();
        telegram.showAlert('Фильтры сохранены!');
      } else {
        throw new Error('Ошибка сохранения');
      }
    } catch (error) {
      telegram.haptic.error();
      telegram.showAlert('Не удалось сохранить фильтры');
    } finally {
      setLoading(false);
    }
  };
  
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 px-4 py-6">
      
      {/* Заголовок */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white mb-2">
          Фильтры поиска
        </h1>
        <p className="text-white/60 text-sm">
          Настройте, кого вы хотите видеть
        </p>
      </div>
      
      {/* Карточка с фильтрами */}
      <div className="backdrop-blur-md bg-white/10 border border-white/20 rounded-3xl p-6 shadow-glass mb-6">
        
        {/* 1. Кого ищем */}
        <div className="mb-8">
          <label className="block text-white font-medium mb-3">
            Кого вы ищете?
          </label>
          
          <div className="grid grid-cols-3 gap-3">
            <button
              onClick={() => setFilters({...filters, looking_for: 'male'})}
              className={`py-3 px-4 rounded-2xl font-medium transition-all ${
                filters.looking_for === 'male'
                  ? 'bg-indigo-500 text-white'
                  : 'bg-white/10 text-white/70 hover:bg-white/20'
              }`}
            >
              Мужчины
            </button>
            
            <button
              onClick={() => setFilters({...filters, looking_for: 'female'})}
              className={`py-3 px-4 rounded-2xl font-medium transition-all ${
                filters.looking_for === 'female'
                  ? 'bg-indigo-500 text-white'
                  : 'bg-white/10 text-white/70 hover:bg-white/20'
              }`}
            >
              Женщины
            </button>
            
            <button
              onClick={() => setFilters({...filters, looking_for: 'everyone'})}
              className={`py-3 px-4 rounded-2xl font-medium transition-all ${
                filters.looking_for === 'everyone'
                  ? 'bg-indigo-500 text-white'
                  : 'bg-white/10 text-white/70 hover:bg-white/20'
              }`}
            >
              Все
            </button>
          </div>
        </div>
        
        {/* 2. Возраст */}
        <div className="mb-8">
          <label className="block text-white font-medium mb-3">
            Возраст: {filters.age_min} - {filters.age_max} лет
          </label>
          
          <div className="space-y-4">
            {/* Минимальный возраст */}
            <div>
              <div className="flex justify-between text-sm text-white/60 mb-2">
                <span>От</span>
                <span>{filters.age_min} лет</span>
              </div>
              <input
                type="range"
                min="18"
                max="80"
                value={filters.age_min}
                onChange={(e) => {
                  const newMin = parseInt(e.target.value);
                  setFilters({
                    ...filters,
                    age_min: newMin,
                    age_max: Math.max(newMin, filters.age_max)
                  });
                }}
                className="w-full h-2 bg-white/20 rounded-full appearance-none cursor-pointer accent-indigo-500"
              />
            </div>
            
            {/* Максимальный возраст */}
            <div>
              <div className="flex justify-between text-sm text-white/60 mb-2">
                <span>До</span>
                <span>{filters.age_max} лет</span>
              </div>
              <input
                type="range"
                min="18"
                max="80"
                value={filters.age_max}
                onChange={(e) => {
                  const newMax = parseInt(e.target.value);
                  setFilters({
                    ...filters,
                    age_max: newMax,
                    age_min: Math.min(newMax, filters.age_min)
                  });
                }}
                className="w-full h-2 bg-white/20 rounded-full appearance-none cursor-pointer accent-indigo-500"
              />
            </div>
          </div>
        </div>
        
        {/* 3. Расстояние */}
        <div className="mb-8">
          <label className="block text-white font-medium mb-3">
            Максимальное расстояние: {filters.max_distance} км
          </label>
          
          <input
            type="range"
            min="1"
            max="200"
            step="5"
            value={filters.max_distance}
            onChange={(e) => setFilters({...filters, max_distance: parseInt(e.target.value)})}
            className="w-full h-2 bg-white/20 rounded-full appearance-none cursor-pointer accent-indigo-500"
          />
          
          <div className="flex justify-between text-xs text-white/50 mt-2">
            <span>1 км</span>
            <span>50 км</span>
            <span>100 км</span>
            <span>200 км</span>
          </div>
        </div>
        
        {/* 4. Интересы (опционально) */}
        <div>
          <label className="block text-white font-medium mb-3">
            Обязательные интересы (необязательно)
          </label>
          
          <div className="flex flex-wrap gap-2">
            {['Путешествия', 'Спорт', 'Музыка', 'Кино', 'Искусство', 'Кулинария', 'Йога', 'Чтение'].map((interest) => (
              <button
                key={interest}
                onClick={() => {
                  const isSelected = filters.required_interests.includes(interest);
                  setFilters({
                    ...filters,
                    required_interests: isSelected
                      ? filters.required_interests.filter(i => i !== interest)
                      : [...filters.required_interests, interest]
                  });
                }}
                className={`px-4 py-2 rounded-full text-sm font-medium transition-all ${
                  filters.required_interests.includes(interest)
                    ? 'bg-indigo-500/40 text-indigo-200 border border-indigo-400/50'
                    : 'bg-white/10 text-white/60 border border-white/20 hover:bg-white/20'
                }`}
              >
                {interest}
              </button>
            ))}
          </div>
          
          {filters.required_interests.length > 0 && (
            <p className="text-white/50 text-xs mt-3">
              Будут показаны только пользователи с этими интересами
            </p>
          )}
        </div>
        
      </div>
      
      {/* Информация */}
      <div className="backdrop-blur-md bg-indigo-500/20 border border-indigo-400/30 rounded-2xl p-4 mb-6">
        <div className="flex gap-3">
          <svg className="w-5 h-5 text-indigo-300 flex-shrink-0 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
          </svg>
          <div>
            <p className="text-indigo-200 text-sm font-medium mb-1">
              Умный поиск
            </p>
            <p className="text-indigo-200/70 text-xs leading-relaxed">
              Система автоматически учитывает взаимные предпочтения. 
              Вы увидите тех, кто тоже подходит под ваши критерии.
            </p>
          </div>
        </div>
      </div>
      
      {/* Кнопка сохранения */}
      <button
        onClick={saveFilters}
        disabled={loading}
        className={`w-full py-4 rounded-2xl font-semibold text-white transition-all ${
          loading
            ? 'bg-white/20 cursor-not-allowed'
            : 'bg-indigo-600 hover:bg-indigo-500 active:scale-98'
        }`}
      >
        {loading ? 'Сохранение...' : 'Сохранить фильтры'}
      </button>
      
      {/* Статистика */}
      <div className="mt-6 text-center">
        <p className="text-white/50 text-sm">
          С этими фильтрами доступно ~247 профилей поблизости
        </p>
      </div>
      
    </div>
  );
};

export default FilterSettings;
