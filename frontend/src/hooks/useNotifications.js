import { useEffect, useState } from 'react';

export const useNotifications = () => {
    const [permission, setPermission] = useState('default');

    useEffect(() => {
        if ("Notification" in window) {
            setPermission(Notification.permission);
            if (Notification.permission === "default") {
                Notification.requestPermission().then(perm => {
                    setPermission(perm);
                });
            }
        }
    }, []);

    const sendAlert = (title, body) => {
        if (permission === 'granted' && "Notification" in window) {
            new Notification(title, { body, icon: '/vite.svg' }); // Add proper icon when available
        }
    };

    return { permission, sendAlert };
};
