/**
 * Premium Dating TWA - Discovery Screen
 * Tinder-like swipe interface with Glassmorphism design
 */

import React, { useState, useEffect, useRef } from 'react';
import { motion, useMotionValue, useTransform } from 'framer-motion';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

// User Card Component with Premium Glassmorphism
const UserCard = ({ user, onSwipe }) => {
  const [currentPhotoIndex, setCurrentPhotoIndex] = useState(0);
  const x = useMotionValue(0);
  const rotate = useTransform(x, [-200, 200], [-30, 30]);
  const opacity = useTransform(x, [-200, -100, 0, 100, 200], [0, 1, 1, 1, 0]);
  
  const cardRef = useRef(null);
  
  const handleDragEnd = (event, info) => {
    const offset = info.offset.x;
    const velocity = info.velocity.x;
    
    // Swipe threshold
    if (Math.abs(offset) > 100 || Math.abs(velocity) > 500) {
      const direction = offset > 0 ? 'like' : 'dislike';
      onSwipe(user.id, direction);
    }
  };
  
  const nextPhoto = () => {
    setCurrentPhotoIndex((prev) => 
      prev < user.photos_urls.length - 1 ? prev + 1 : 0
    );
  };
  
  const prevPhoto = () => {
    setCurrentPhotoIndex((prev) => 
      prev > 0 ? prev - 1 : user.photos_urls.length - 1
    );
  };
  
  return (
    <motion.div
      ref={cardRef}
      className="absolute w-full h-full"
      style={{ x, rotate, opacity }}
      drag="x"
      dragConstraints={{ left: 0, right: 0 }}
      onDragEnd={handleDragEnd}
      whileTap={{ cursor: 'grabbing' }}
    >
      {/* Card Container - Glassmorphism Effect */}
      <div className="relative w-full h-full rounded-3xl overflow-hidden shadow-2xl border border-white/20 backdrop-blur-xl bg-gradient-to-br from-slate-900/90 to-slate-800/80">
        
        {/* Photo Background with Gradient Overlay */}
        <div className="absolute inset-0">
          <img 
            src={user.photos_urls[currentPhotoIndex]} 
            alt={user.first_name}
            className="w-full h-full object-cover"
          />
          {/* Dark gradient overlay for text readability */}
          <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent" />
        </div>
        
        {/* Photo Navigation Areas (invisible tap zones) */}
        <div className="absolute inset-0 flex">
          <button 
            onClick={prevPhoto}
            className="w-1/3 h-full focus:outline-none"
            aria-label="Previous photo"
          />
          <div className="w-1/3 h-full" />
          <button 
            onClick={nextPhoto}
            className="w-1/3 h-full focus:outline-none"
            aria-label="Next photo"
          />
        </div>
        
        {/* Photo Indicators */}
        <div className="absolute top-4 left-4 right-4 flex gap-1 z-10">
          {user.photos_urls.map((_, index) => (
            <div 
              key={index}
              className={`h-1 flex-1 rounded-full transition-all duration-300 ${
                index === currentPhotoIndex 
                  ? 'bg-white' 
                  : 'bg-white/30'
              }`}
            />
          ))}
        </div>
        
        {/* Swipe Direction Indicators */}
        <motion.div 
          className="absolute top-1/4 left-8 text-6xl font-black text-green-500 opacity-0"
          style={{ 
            opacity: useTransform(x, [0, 100], [0, 1]),
            rotate: -20
          }}
        >
          LIKE
        </motion.div>
        
        <motion.div 
          className="absolute top-1/4 right-8 text-6xl font-black text-red-500 opacity-0"
          style={{ 
            opacity: useTransform(x, [-100, 0], [1, 0]),
            rotate: 20
          }}
        >
          NOPE
        </motion.div>
        
        {/* User Info - Glassmorphic Card at Bottom */}
        <div className="absolute bottom-0 left-0 right-0 p-6 z-20">
          <div className="backdrop-blur-md bg-white/10 border border-white/20 rounded-2xl p-5 shadow-lg">
            
            {/* Name & Age */}
            <div className="flex items-baseline gap-2 mb-3">
              <h2 className="text-3xl font-bold text-white">
                {user.first_name}
              </h2>
              <span className="text-2xl text-white/90">
                {user.age}
              </span>
              {user.verified && (
                <svg className="w-6 h-6 text-indigo-400" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                </svg>
              )}
            </div>
            
            {/* Bio */}
            {user.bio && (
              <p className="text-white/90 text-base mb-3 line-clamp-2">
                {user.bio}
              </p>
            )}
            
            {/* Location & Distance */}
            <div className="flex items-center gap-2 text-white/80 text-sm mb-3">
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M5.05 4.05a7 7 0 119.9 9.9L10 18.9l-4.95-4.95a7 7 0 010-9.9zM10 11a2 2 0 100-4 2 2 0 000 4z" clipRule="evenodd" />
              </svg>
              <span>{user.city}</span>
              <span>•</span>
              <span>{user.distance ?? '?'} km away</span>
            </div>
            
            {/* Interests Tags */}
            <div className="flex flex-wrap gap-2">
              {user.interests.slice(0, 4).map((interest, index) => (
                <span 
                  key={index}
                  className="px-3 py-1 rounded-full text-xs font-medium bg-indigo-500/30 text-indigo-200 border border-indigo-400/30 backdrop-blur-sm"
                >
                  {interest}
                </span>
              ))}
              {user.interests.length > 4 && (
                <span className="px-3 py-1 rounded-full text-xs font-medium text-white/60">
                  +{user.interests.length - 4} more
                </span>
              )}
            </div>
          </div>
        </div>
        
        {/* Action Buttons Overlay (Bottom Right - Info Button) */}
        <button 
          className="absolute bottom-8 right-6 z-30 w-12 h-12 rounded-full backdrop-blur-md bg-white/20 border border-white/30 flex items-center justify-center shadow-lg hover:bg-white/30 transition-colors"
          onClick={() => console.log('Show full profile')}
        >
          <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
        </button>
      </div>
    </motion.div>
  );
};


// Main Discovery Component
const Discovery = () => {
  const [users, setUsers] = useState([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  
  useEffect(() => {
    fetchPotentialMatches();
  }, []);
  
  const fetchPotentialMatches = async () => {
    try {
      // Get Telegram Web App data for authentication
      const webApp = window.Telegram?.WebApp;
      const initData = webApp?.initData;
      
      const response = await fetch(`${API_BASE_URL}/api/discover`, {
        headers: {
          'Authorization': `tma ${initData}`,
          'Content-Type': 'application/json'
        }
      });
      
      const data = await response.json();
      setUsers(data.users);
      setLoading(false);
    } catch (error) {
      console.error('Failed to fetch matches:', error);
      setLoading(false);
    }
  };
  
  const handleSwipe = async (userId, direction) => {
    try {
      const webApp = window.Telegram?.WebApp;
      const initData = webApp?.initData;
      
      // Send swipe to backend
      const response = await fetch(`${API_BASE_URL}/api/swipe`, {
        method: 'POST',
        headers: {
          'Authorization': `tma ${initData}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          target_user_id: userId,
          swipe_type: direction
        })
      });
      
      const result = await response.json();
      
      // Check if it's a match!
      if (result.is_match) {
        showMatchNotification(result.matched_user);
      }
      
      // Move to next card
      setCurrentIndex(prev => prev + 1);
      
      // Load more users if running low
      if (currentIndex >= users.length - 3) {
        fetchPotentialMatches();
      }
    } catch (error) {
      console.error('Swipe failed:', error);
    }
  };
  
  const handleLike = () => {
    if (currentIndex < users.length) {
      handleSwipe(users[currentIndex].id, 'like');
    }
  };
  
  const handleDislike = () => {
    if (currentIndex < users.length) {
      handleSwipe(users[currentIndex].id, 'dislike');
    }
  };
  
  const handleSuperLike = () => {
    if (currentIndex < users.length) {
      handleSwipe(users[currentIndex].id, 'superlike');
    }
  };
  
  const showMatchNotification = (matchedUser) => {
    // Trigger haptic feedback
    window.Telegram?.WebApp?.HapticFeedback.notificationOccurred('success');
    
    // Show match modal (implement separately)
    console.log('New match!', matchedUser);
  };
  
  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-gradient-to-br from-slate-900 to-slate-800">
        <div className="animate-pulse text-indigo-400 text-xl">
          Loading incredible people...
        </div>
      </div>
    );
  }
  
  return (
    <div className="relative h-screen bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-900 overflow-hidden">
      
      {/* Animated Background Elements */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div className="absolute top-20 left-10 w-72 h-72 bg-indigo-500/20 rounded-full blur-3xl animate-pulse" />
        <div className="absolute bottom-20 right-10 w-96 h-96 bg-purple-500/20 rounded-full blur-3xl animate-pulse delay-1000" />
      </div>
      
      {/* Header */}
      <header className="relative z-10 p-6 flex justify-between items-center">
        <div className="backdrop-blur-md bg-white/10 border border-white/20 rounded-2xl px-4 py-2">
          <h1 className="text-2xl font-bold bg-gradient-to-r from-indigo-400 to-purple-400 bg-clip-text text-transparent">
            Premium Dating
          </h1>
        </div>
        
        <button className="backdrop-blur-md bg-white/10 border border-white/20 rounded-full p-3 hover:bg-white/20 transition-colors">
          <svg className="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
          </svg>
        </button>
      </header>
      
      {/* Card Stack Container */}
      <div className="relative mx-auto max-w-md h-[calc(100vh-200px)] px-4">
        {users.slice(currentIndex, currentIndex + 3).reverse().map((user, index) => (
          <UserCard 
            key={user.id} 
            user={user} 
            onSwipe={handleSwipe}
          />
        ))}
        
        {/* Empty State */}
        {currentIndex >= users.length && (
          <div className="flex items-center justify-center h-full">
            <div className="backdrop-blur-md bg-white/10 border border-white/20 rounded-3xl p-8 text-center">
              <p className="text-white text-xl mb-4">You've seen everyone nearby!</p>
              <button 
                onClick={fetchPotentialMatches}
                className="px-6 py-3 bg-indigo-600 hover:bg-indigo-500 text-white rounded-full font-semibold transition-colors"
              >
                Expand Search
              </button>
            </div>
          </div>
        )}
      </div>
      
      {/* Action Buttons */}
      <div className="absolute bottom-8 left-0 right-0 flex justify-center items-center gap-6 px-4">
        {/* Dislike Button */}
        <button 
          onClick={handleDislike}
          className="w-16 h-16 rounded-full backdrop-blur-md bg-white/10 border border-white/20 flex items-center justify-center shadow-lg hover:bg-red-500/20 hover:border-red-400/40 transition-all group"
        >
          <svg className="w-8 h-8 text-red-400 group-hover:scale-110 transition-transform" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
          </svg>
        </button>
        
        {/* Super Like Button */}
        <button 
          onClick={handleSuperLike}
          className="w-14 h-14 rounded-full backdrop-blur-md bg-gradient-to-br from-indigo-500/30 to-purple-500/30 border border-indigo-400/40 flex items-center justify-center shadow-lg hover:from-indigo-500/50 hover:to-purple-500/50 transition-all group"
        >
          <svg className="w-7 h-7 text-indigo-300 group-hover:scale-110 transition-transform" fill="currentColor" viewBox="0 0 20 20">
            <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
          </svg>
        </button>
        
        {/* Like Button */}
        <button 
          onClick={handleLike}
          className="w-16 h-16 rounded-full backdrop-blur-md bg-white/10 border border-white/20 flex items-center justify-center shadow-lg hover:bg-green-500/20 hover:border-green-400/40 transition-all group"
        >
          <svg className="w-8 h-8 text-green-400 group-hover:scale-110 transition-transform" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M3.172 5.172a4 4 0 015.656 0L10 6.343l1.172-1.171a4 4 0 115.656 5.656L10 17.657l-6.828-6.829a4 4 0 010-5.656z" clipRule="evenodd" />
          </svg>
        </button>
      </div>
    </div>
  );
};

export default Discovery;
