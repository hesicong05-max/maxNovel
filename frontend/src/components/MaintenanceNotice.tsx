import { useEffect, useId, useRef } from "react";
import type { ApiError } from "@/services/api";

interface MaintenanceNoticeProps {
  error: ApiError;
  draftStored: boolean;
  onCopy: () => void;
  onRetry?: () => void;
  onBack?: () => void;
  focusOnMount?: boolean;
}

export default function MaintenanceNotice({
  error,
  draftStored,
  onCopy,
  onRetry,
  onBack,
  focusOnMount = false,
}: MaintenanceNoticeProps) {
  const noticeRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (focusOnMount) noticeRef.current?.focus();
  }, [focusOnMount]);

  return (
    <div
      ref={noticeRef}
      className="draft-notice draft-notice--maintenance"
      role="alert"
      tabIndex={-1}
      aria-labelledby={titleId}
    >
      <h3 id={titleId}>项目正在维护，暂时无法保存</h3>
      <p>
        {draftStored
          ? "未保存的内容已保留在此设备。"
          : "本地草稿也未能保存，请立即复制内容，避免丢失。"}
      </p>
      <p className="draft-notice__hint">
        {error.retryable
          ? "请稍后手动重试，系统不会自动重复提交。"
          : "当前内容无法提交，请先保留副本，稍后再返回处理。"}
      </p>
      {error.eventId && (
        <p className="draft-notice__event">
          问题编号：<code>{error.eventId}</code>
        </p>
      )}
      <div className="draft-notice__actions">
        <button
          type="button"
          className={`btn${draftStored ? "" : " btn-primary"}`}
          onClick={onCopy}
        >
          复制未保存内容
        </button>
        {error.retryable && onRetry && (
          <button
            type="button"
            className={`btn${draftStored ? " btn-primary" : ""}`}
            onClick={onRetry}
          >
            手动重试保存
          </button>
        )}
        {onBack && (
          <button type="button" className="btn btn-ghost" onClick={onBack}>
            返回
          </button>
        )}
      </div>
    </div>
  );
}
