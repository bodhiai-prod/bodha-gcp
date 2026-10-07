import React, { useMemo, useState } from "react";
import { Books, CaretDown, CheckCircle, Clock, SpinnerGap, X } from "@phosphor-icons/react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Checkbox } from "@/components/ui/checkbox";

const STATUS_LABELS = {
  indexed: "Ready",
  queued: "Queued",
  ingesting: "Indexing",
  topics_extracting: "Extracting topics",
  extraction_failed: "Needs attention",
};

const statusLabel = (book) => STATUS_LABELS[book.status] || book.status || "Processing";
const statusIcon = (book) => {
  if (book.status === "indexed") return <CheckCircle weight="fill" aria-hidden="true" />;
  if (["queued", "ingesting", "topics_extracting"].includes(book.status)) return <SpinnerGap className="text-[#002FA7] animate-spin" aria-hidden="true" />;
  return <Clock aria-hidden="true" />;
};

/** A compact, searchable multi-selector for the paper source textbooks. */
export default function TextbookMultiSelect({ textbooks, selectedIds, onChange }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const selectedBooks = useMemo(
    () => selectedIds.map((id) => textbooks.find((book) => book.id === id)).filter(Boolean),
    [selectedIds, textbooks]
  );
  const filteredTextbooks = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    if (!normalizedQuery) return textbooks;
    return textbooks.filter((book) => `${book.original_filename} ${book.subject} ${book.class_name}`
      .toLowerCase()
      .includes(normalizedQuery));
  }, [query, textbooks]);

  const toggleBook = (id) => {
    onChange(selectedIds.includes(id)
      ? selectedIds.filter((selectedId) => selectedId !== id)
      : [...selectedIds, id]);
  };

  return (
    <div className="textbook-selector" data-testid="textbook-multi-select">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="textbook-selector-trigger"
            aria-label="Select textbooks"
            aria-haspopup="dialog"
            data-testid="paper-textbook-select"
          >
            <Books size={20} weight="duotone" aria-hidden="true" />
            <span>{selectedIds.length ? `${selectedIds.length} textbook${selectedIds.length === 1 ? "" : "s"} selected` : "Select textbooks"}</span>
            <CaretDown size={16} weight="bold" aria-hidden="true" />
          </button>
        </PopoverTrigger>
        <PopoverContent align="start" className="textbook-selector-popover" data-testid="textbook-picker-popover">
          <Command shouldFilter={false}>
            <div className="textbook-selector-heading">
              <div>
                <span className="overline">Textbook library</span>
                <strong>Choose paper sources</strong>
              </div>
              <span aria-live="polite">{selectedIds.length} selected</span>
            </div>
            <CommandInput
              value={query}
              onValueChange={setQuery}
              placeholder="Search title, subject, or class…"
              aria-label="Search textbooks"
              data-testid="textbook-search-input"
            />
            <CommandList className="textbook-selector-results">
              <CommandEmpty>No textbooks match “{query}”.</CommandEmpty>
              {filteredTextbooks.map((book) => {
                const checked = selectedIds.includes(book.id);
                const searchValue = `${book.original_filename} ${book.subject} ${book.class_name}`;
                return (
                  <CommandItem
                    key={book.id}
                    value={searchValue}
                    onSelect={() => toggleBook(book.id)}
                    className="textbook-picker-row"
                    role="menuitemcheckbox"
                    aria-checked={checked}
                    data-testid={`textbook-picker-option-${book.id}`}
                  >
                    <Checkbox checked={checked} tabIndex={-1} aria-hidden="true" />
                    <div className="textbook-picker-copy">
                      <strong title={book.original_filename}>{book.original_filename}</strong>
                      <span>{book.subject || "Uncategorised"} · Class {book.class_name || "—"}</span>
                    </div>
                    <span className={`textbook-status textbook-status-${book.status || "unknown"}`} title={statusLabel(book)}>
                      {statusIcon(book)} {statusLabel(book)}
                      {book.topic_count > 0 && <small>{book.topic_count} topics</small>}
                    </span>
                  </CommandItem>
                );
              })}
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>

      {selectedBooks.length > 0 && (
        <ul className="selected-textbook-list" aria-label="Selected textbooks" data-testid="selected-textbooks-chips">
          {selectedBooks.map((book) => (
            <li key={book.id} className="selected-textbook-card" data-testid={`textbook-chip-${book.id}`}>
              <Books size={18} weight="duotone" aria-hidden="true" />
              <div>
                <strong title={book.original_filename}>{book.original_filename}</strong>
                <span>{book.subject || "Uncategorised"} · Class {book.class_name || "—"}</span>
              </div>
              <button
                type="button"
                onClick={() => toggleBook(book.id)}
                aria-label={`Remove ${book.original_filename}`}
                className="selected-textbook-remove"
                data-testid={`textbook-chip-remove-${book.id}`}
              >
                <X size={16} weight="bold" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
