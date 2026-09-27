import { Component, type ReactNode } from "react";

/** Сбой в панели не должен ронять пульт: карта и лента продолжают работать. */
export class ErrorBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error("Панель упала:", error);
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
