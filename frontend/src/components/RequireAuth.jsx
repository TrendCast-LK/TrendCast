import { Navigate, useLocation } from "react-router-dom";
import { useAuth, isLoggingOut } from "../context/AuthContext";

export default function RequireAuth({ children }) {
  const { isAuthenticated } = useAuth();
  const location = useLocation();

  if (!isAuthenticated) {
    // Don't record "from" during a deliberate logout — see the comment on
    // AuthContext's logout() for why. A genuine "session expired, please log
    // back in" redirect still records it normally.
    const state = isLoggingOut() ? undefined : { from: location };
    return <Navigate to="/" replace state={state} />;
  }

  return children;
}
