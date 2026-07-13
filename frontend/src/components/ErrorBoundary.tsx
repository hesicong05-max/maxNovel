import { Component, ErrorInfo, ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

/**
 * Global Error Boundary — catches unhandled React render errors
 * and displays a user-friendly fallback UI instead of a white screen.
 */
class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error("[ErrorBoundary] Unhandled render error:", error, errorInfo);
    this.setState({ error, errorInfo });
  }

  handleReload = (): void => {
    window.location.reload();
  };

  handleGoHome = (): void => {
    window.location.href = "/";
  };

  render(): ReactNode {
    if (!this.state.hasError) return this.props.children;

    const { error, errorInfo } = this.state;

    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
          padding: "2rem",
          fontFamily: "system-ui, -apple-system, sans-serif",
          backgroundColor: "#1a1a2e",
          color: "#e0e0e0",
        }}
      >
        <div
          style={{
            maxWidth: "600px",
            textAlign: "center",
            backgroundColor: "#16213e",
            borderRadius: "12px",
            padding: "2.5rem",
            boxShadow: "0 8px 32px rgba(0,0,0,0.3)",
          }}
        >
          <div style={{ fontSize: "3rem", marginBottom: "1rem" }}>⚠️</div>
          <h1
            style={{
              fontSize: "1.5rem",
              margin: "0 0 0.5rem",
              color: "#B8860B",
            }}
          >
            页面出错了
          </h1>
          <p
            style={{
              color: "#a0a0a0",
              fontSize: "0.95rem",
              lineHeight: 1.6,
              marginBottom: "1.5rem",
            }}
          >
            应用遇到了一个意外错误。你可以刷新页面重试，或者返回首页。
          </p>

          {error && (
            <details
              style={{
                textAlign: "left",
                backgroundColor: "#0f3460",
                borderRadius: "8px",
                padding: "1rem",
                marginBottom: "1.5rem",
                fontSize: "0.85rem",
                color: "#7ec8e3",
              }}
            >
              <summary style={{ cursor: "pointer", fontWeight: 600 }}>
                错误详情
              </summary>
              <pre
                style={{
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  marginTop: "0.75rem",
                  fontSize: "0.8rem",
                  lineHeight: 1.5,
                }}
              >
                {error.toString()}
                {errorInfo?.componentStack}
              </pre>
            </details>
          )}

          <div style={{ display: "flex", gap: "0.75rem", justifyContent: "center" }}>
            <button
              onClick={this.handleReload}
              style={{
                backgroundColor: "#B8860B",
                color: "#fff",
                border: "none",
                borderRadius: "8px",
                padding: "0.6rem 1.5rem",
                fontSize: "0.95rem",
                cursor: "pointer",
                fontWeight: 600,
              }}
            >
              刷新页面
            </button>
            <button
              onClick={this.handleGoHome}
              style={{
                backgroundColor: "transparent",
                color: "#a0a0a0",
                border: "1px solid #444",
                borderRadius: "8px",
                padding: "0.6rem 1.5rem",
                fontSize: "0.95rem",
                cursor: "pointer",
              }}
            >
              返回首页
            </button>
          </div>
        </div>
      </div>
    );
  }
}

export default ErrorBoundary;
