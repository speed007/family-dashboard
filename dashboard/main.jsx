import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import PortraitKiosk from './views/PortraitKiosk.jsx';
import MobileDashboard from './views/MobileDashboard.jsx';

ReactDOM.createRoot(document.getElementById('root')).render(
  React.createElement(
    Router,
    null,
    React.createElement(
      Routes,
      null,
      /* 🖥️ Dedicated 1080x1920 Kiosk Layout */
      React.createElement(Route, { path: "/kiosk", element: React.createElement(PortraitKiosk) }),
      
      /* 📱 Universal Fluid Grid Dashboard Layout */
      React.createElement(Route, { path: "/dashboard", element: React.createElement(MobileDashboard) }),
      
      /* Redirect any raw root requests cleanly to the mobile layout */
      React.createElement(Route, { path: "*", element: React.createElement(Navigate, { to: "/dashboard", replace: true }) })
    )
  )
);