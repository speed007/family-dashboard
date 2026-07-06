import React, { useState, useEffect } from 'react';
import mqtt from 'mqtt';

const FamilyDashboard = () => {
  const [shoppingList, setShoppingList] = useState([]);
  const [appointments, setAppointments] = useState([]);
  const [meals, setMeals] = useState([]);
  const [currentTime, setCurrentTime] = useState(new Date());
  const [dashboardPower, setDashboardPower] = useState('on');
  const [connected, setConnected] = useState(false);
  // Pull connection details from environment variables set at build time
  // (see .env / CRA's REACT_APP_ prefix convention) instead of hardcoding
  // credentials directly in source.
  const MQTT_BROKER = import.meta.env.VITE_MQTT_BROKER_WS || 'ws://192.168.102.112:9001';
  const MQTT_USER = import.meta.env.VITE_MQTT_USER || 'mqtt_user';
  const MQTT_PASS = import.meta.env.VITE_MQTT_PASS || '';

  // ==================== MQTT CONNECTION ====================
  useEffect(() => {
    const client = mqtt.connect(MQTT_BROKER, {
      username: MQTT_USER,
      password: MQTT_PASS,
      clean: true,
      reconnectPeriod: 1000,
    });

    client.on('connect', () => {
      console.log('✅ Connected to MQTT');
      setConnected(true);

      // Subscribe to topics. These match exactly what telegram_bot.py's
      // publish_dashboard_snapshot() publishes every 30s, plus whatever
      // Home Assistant forwards from ha_automations.yaml.
      client.subscribe('home/dashboard/shopping_list', (err) => {
        if (err) console.error('Subscribe error:', err);
      });
      client.subscribe('home/dashboard/calendar_events', (err) => {
        if (err) console.error('Subscribe error:', err);
      });
      client.subscribe('home/dashboard/meal_plan', (err) => {
        if (err) console.error('Subscribe error:', err);
      });
      client.subscribe('home/dashboard/kitchen/power', (err) => {
        if (err) console.error('Subscribe error:', err);
      });
    });

    client.on('message', (topic, message) => {
      const payload = message.toString();
      console.log(`📨 ${topic}: ${payload}`);

      try {
        if (topic === 'home/dashboard/shopping_list') {
          const data = JSON.parse(payload);
          if (data.items) {
            setShoppingList(data.items);
          }
        } else if (topic === 'home/dashboard/calendar_events') {
          const data = JSON.parse(payload);
          if (data.events) {
            setAppointments(data.events);
          }
        } else if (topic === 'home/dashboard/meal_plan') {
          // telegram_bot.py publishes { meals: [{meal, date, type}, ...], timestamp }
          const data = JSON.parse(payload);
          if (data.meals) {
            setMeals(data.meals);
          }
        } else if (topic === 'home/dashboard/kitchen/power') {
          setDashboardPower(payload);
          console.log(`Power command: ${payload}`);
        }
      } catch (err) {
        console.error('Parse error:', err);
      }
    });

    client.on('error', (err) => {
      console.error('MQTT Error:', err);
    });

    client.on('disconnect', () => {
      setConnected(false);
      console.log('❌ Disconnected from MQTT');
    });

    return () => {
      client.end();
    };
  }, [MQTT_BROKER, MQTT_USER, MQTT_PASS]);

  // ==================== CLOCK ====================
  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentTime(new Date());
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  // ==================== FORMATTING HELPERS ====================
  const formatDate = (date) => {
    return new Intl.DateTimeFormat('en-US', {
      weekday: 'long',
      month: 'short',
      day: 'numeric',
    }).format(date);
  };

  const formatTime = (date) => {
    return new Intl.DateTimeFormat('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      hour12: true,
    }).format(date);
  };

  const getTodayAndTomorrow = () => {
    const today = new Date();
    const tomorrow = new Date(today);
    tomorrow.setDate(tomorrow.getDate() + 1);
    return { today, tomorrow };
  };

  const { today, tomorrow } = getTodayAndTomorrow();
  const todayStr = today.toISOString().split('T')[0];
  const tomorrowStr = tomorrow.toISOString().split('T')[0];

  // Look up a meal by date + type, returning '—' if nothing's planned yet.
  const getMeal = (dateStr, mealType) => {
    const match = meals.find((m) => m.date === dateStr && m.type === mealType);
    return match ? match.meal : '—';
  };

  // ==================== RENDER ====================
  return (
    <div className="dashboard" style={styles.container}>
      {/* HEADER */}
      <header style={styles.header}>
        <div style={styles.headerContent}>
          <div>
            <h1 style={styles.title}>Family Hub</h1>
            <p style={styles.date}>{formatDate(currentTime)}</p>
          </div>
          <div style={styles.timeContainer}>
            <p style={styles.time}>{formatTime(currentTime)}</p>
            <p style={styles.connectionStatus}>
              {connected ? '🟢 Connected' : '🔴 Offline'}
            </p>
          </div>
        </div>
      </header>

      {/* MAIN CONTENT */}
      <main style={styles.mainContent}>
        {/* LEFT COLUMN: Shopping List */}
        <section style={styles.column}>
          <div style={styles.card}>
            <h2 style={styles.cardTitle}>🛒 Shopping List</h2>
            <div style={styles.listContainer}>
              {shoppingList && shoppingList.length > 0 ? (
                <ul style={styles.list}>
                  {shoppingList.map((item, idx) => (
                    <li key={idx} style={styles.listItem}>
                      <span style={styles.bullet}>•</span>
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p style={styles.emptyState}>No items yet</p>
              )}
            </div>
          </div>
        </section>

        {/* CENTER COLUMN: Calendar & Appointments */}
        <section style={styles.column}>
          <div style={styles.card}>
            <h2 style={styles.cardTitle}>📅 Appointments</h2>
            <div style={styles.listContainer}>
              {/* Today's appointments */}
              <div style={styles.daySection}>
                <h3 style={styles.dayTitle}>Today</h3>
                {appointments && appointments.length > 0 ? (
                  <ul style={styles.list}>
                    {appointments
                      .filter((apt) => apt.date === today.toISOString().split('T')[0])
                      .map((apt, idx) => (
                        <li key={idx} style={styles.appointmentItem}>
                          <span style={styles.time}>{apt.time || '—'}</span>
                          <span style={styles.appointmentTitle}>{apt.title}</span>
                        </li>
                      ))}
                  </ul>
                ) : (
                  <p style={styles.emptyState}>No appointments</p>
                )}
              </div>

              {/* Tomorrow's appointments */}
              <div style={styles.daySection}>
                <h3 style={styles.dayTitle}>Tomorrow</h3>
                {appointments && appointments.length > 0 ? (
                  <ul style={styles.list}>
                    {appointments
                      .filter((apt) => apt.date === tomorrow.toISOString().split('T')[0])
                      .map((apt, idx) => (
                        <li key={idx} style={styles.appointmentItem}>
                          <span style={styles.time}>{apt.time || '—'}</span>
                          <span style={styles.appointmentTitle}>{apt.title}</span>
                        </li>
                      ))}
                  </ul>
                ) : (
                  <p style={styles.emptyState}>No appointments</p>
                )}
              </div>
            </div>
          </div>
        </section>

        {/* RIGHT COLUMN: Meal Plan */}
        <section style={styles.column}>
          <div style={styles.card}>
            <h2 style={styles.cardTitle}>🍽️ Meal Plan</h2>
            <div style={styles.listContainer}>
              {/* Today's meals */}
              <div style={styles.daySection}>
                <h3 style={styles.dayTitle}>Today</h3>
                <div style={styles.mealTypes}>
                  <div style={styles.mealItem}>
                    <span style={styles.mealLabel}>Breakfast</span>
                    <span style={styles.mealValue}>{getMeal(todayStr, 'breakfast')}</span>
                  </div>
                  <div style={styles.mealItem}>
                    <span style={styles.mealLabel}>Lunch</span>
                    <span style={styles.mealValue}>{getMeal(todayStr, 'lunch')}</span>
                  </div>
                  <div style={styles.mealItem}>
                    <span style={styles.mealLabel}>Dinner</span>
                    <span style={styles.mealValue}>{getMeal(todayStr, 'dinner')}</span>
                  </div>
                </div>
              </div>

              {/* Tomorrow's meals */}
              <div style={styles.daySection}>
                <h3 style={styles.dayTitle}>Tomorrow</h3>
                <div style={styles.mealTypes}>
                  <div style={styles.mealItem}>
                    <span style={styles.mealLabel}>Breakfast</span>
                    <span style={styles.mealValue}>{getMeal(tomorrowStr, 'breakfast')}</span>
                  </div>
                  <div style={styles.mealItem}>
                    <span style={styles.mealLabel}>Lunch</span>
                    <span style={styles.mealValue}>{getMeal(tomorrowStr, 'lunch')}</span>
                  </div>
                  <div style={styles.mealItem}>
                    <span style={styles.mealLabel}>Dinner</span>
                    <span style={styles.mealValue}>{getMeal(tomorrowStr, 'dinner')}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>
      </main>

      {/* FOOTER */}
      <footer style={styles.footer}>
        <p style={styles.footerText}>
          💡 Powered by Home Assistant • {connected ? 'Live' : 'Offline'}
        </p>
      </footer>
    </div>
  );
};

// ==================== STYLES ====================
const styles = {
  container: {
    width: '100vw',
    height: '100vh',
    backgroundColor: '#0f1419',
    color: '#e0e0e0',
    fontFamily: "'Segoe UI', Tahoma, Geneva, Verdana, sans-serif",
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },

  header: {
    backgroundColor: '#1a1f2e',
    borderBottom: '3px solid #2196F3',
    padding: '20px 30px',
    minHeight: '80px',
    display: 'flex',
    alignItems: 'center',
  },

  headerContent: {
    width: '100%',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },

  title: {
    margin: 0,
    fontSize: '36px',
    fontWeight: '700',
    color: '#2196F3',
  },

  date: {
    margin: '5px 0 0 0',
    fontSize: '16px',
    color: '#9e9e9e',
  },

  timeContainer: {
    textAlign: 'right',
  },

  time: {
    margin: 0,
    fontSize: '48px',
    fontWeight: '700',
    color: '#fff',
  },

  connectionStatus: {
    margin: '5px 0 0 0',
    fontSize: '14px',
    color: '#4caf50',
  },

  mainContent: {
    flex: 1,
    display: 'grid',
    gridTemplateColumns: 'repeat(3, 1fr)',
    gap: '20px',
    padding: '20px 30px',
    overflow: 'auto',
  },

  column: {
    display: 'flex',
    flexDirection: 'column',
  },

  card: {
    backgroundColor: '#1a1f2e',
    borderRadius: '12px',
    padding: '20px',
    boxShadow: '0 4px 12px rgba(0, 0, 0, 0.3)',
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
  },

  cardTitle: {
    margin: '0 0 20px 0',
    fontSize: '24px',
    fontWeight: '600',
    color: '#fff',
    borderBottom: '2px solid #2196F3',
    paddingBottom: '10px',
  },

  listContainer: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    gap: '15px',
    overflowY: 'auto',
  },

  list: {
    listStyle: 'none',
    margin: 0,
    padding: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  },

  listItem: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    padding: '10px 12px',
    backgroundColor: '#252d3d',
    borderRadius: '8px',
    fontSize: '16px',
    color: '#e0e0e0',
  },

  bullet: {
    fontSize: '20px',
    color: '#2196F3',
  },

  emptyState: {
    fontSize: '14px',
    color: '#9e9e9e',
    fontStyle: 'italic',
    margin: 0,
  },

  daySection: {
    display: 'flex',
    flexDirection: 'column',
    gap: '10px',
  },

  dayTitle: {
    margin: 0,
    fontSize: '14px',
    fontWeight: '600',
    color: '#2196F3',
    textTransform: 'uppercase',
    letterSpacing: '1px',
  },

  appointmentItem: {
    display: 'flex',
    alignItems: 'center',
    gap: '10px',
    padding: '10px 12px',
    backgroundColor: '#252d3d',
    borderRadius: '8px',
    fontSize: '15px',
  },

  appointmentTitle: {
    flex: 1,
    color: '#e0e0e0',
  },

  mealTypes: {
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  },

  mealItem: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '10px 12px',
    backgroundColor: '#252d3d',
    borderRadius: '8px',
    fontSize: '15px',
  },

  mealLabel: {
    color: '#9e9e9e',
    fontWeight: '500',
  },

  mealValue: {
    color: '#2196F3',
    fontWeight: '600',
  },

  footer: {
    backgroundColor: '#1a1f2e',
    borderTop: '2px solid #2196F3',
    padding: '15px 30px',
    textAlign: 'center',
    minHeight: '50px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },

  footerText: {
    margin: 0,
    fontSize: '14px',
    color: '#9e9e9e',
  },
};

export default FamilyDashboard;
