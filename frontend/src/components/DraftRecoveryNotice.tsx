import { useId } from "react";

export type DraftRecoveryState = "available" | "expired" | "conflict";

interface DraftRecoveryNoticeProps {
  state: DraftRecoveryState;
  savedAt: string;
  onRestore: () => void;
  onCopy: () => void;
  onDiscard: () => void;
  liveMessage?: string;
}

const messages: Record<DraftRecoveryState, string> = {
  available: "发现一份尚未保存到项目的本地草稿，你可以先载入检查。",
  expired:
    "发现一份超过保留期限的本地草稿。草稿仍可载入或复制，只有确认后才会删除。",
  conflict: "本地草稿与项目中已保存的内容不同，请载入副本后人工核对。",
};

export default function DraftRecoveryNotice({
  state,
  savedAt,
  onRestore,
  onCopy,
  onDiscard,
  liveMessage,
}: DraftRecoveryNoticeProps) {
  const titleId = useId();
  const savedAtDisplay = (() => {
    const value = new Date(savedAt);
    return Number.isNaN(value.getTime())
      ? "时间未知"
      : new Intl.DateTimeFormat("zh-CN", {
          dateStyle: "medium",
          timeStyle: "short",
        }).format(value);
  })();
  const discard = () => {
    const confirmed = window.confirm(
      "只删除本地草稿，不影响项目中已保存的内容。确定继续吗？"
    );
    if (confirmed) onDiscard();
  };

  return (
    <section
      className={`draft-notice draft-notice--recovery draft-notice--${state}`}
      role="status"
      aria-live="polite"
      aria-labelledby={titleId}
    >
      <h3 id={titleId}>发现本地草稿</h3>
      <p>{messages[state]}</p>
      <p className="draft-notice__hint">
        本地保存时间：<time dateTime={savedAt}>{savedAtDisplay}</time>
      </p>
      <div className="draft-notice__actions">
        <button type="button" className="btn btn-primary" onClick={onRestore}>
          载入本地副本
        </button>
        <button type="button" className="btn" onClick={onCopy}>
          复制草稿
        </button>
        <button type="button" className="btn btn-ghost" onClick={discard}>
          丢弃本地草稿
        </button>
      </div>
      <span className="sr-only">{liveMessage}</span>
    </section>
  );
}
