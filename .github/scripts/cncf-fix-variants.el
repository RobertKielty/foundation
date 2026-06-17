;;; cncf-fix-variants.el --- Interactive fixer for project-maintainers.csv variants

;; Provides completing-read–driven commands to pick a canonical spelling for
;; each company-name or given-name inconsistency discovered by the flycheck
;; checker, then apply that choice to every diverging row in the file.
;;
;; ── Setup (Doom Emacs config.el) ────────────────────────────────────────────
;;
;;   (after! csv-mode
;;     (load! "~/.../foundation/.github/scripts/cncf-fix-variants"))
;;
;; ── Key bindings (active only in project-maintainers.csv) ───────────────────
;;
;;   C-c f c   cncf/fix-company-variants        company-name casing & cross-project fixes
;;   C-c f n   cncf/fix-name-variants           given-name spelling fixes
;;   C-c f a   cncf/fix-all-variants            both, in sequence
;;   C-c f l   cncf/lookup-maintainer-company   gitdm affiliation history for row at point
;;
;; ── Workflow ─────────────────────────────────────────────────────────────────
;;
;; 1. Open project-maintainers.csv (csv-mode activates the flycheck checker).
;; 2. Review warnings — do your own research on the correct canonical form.
;; 3. Call one of the fix commands.  For each inconsistency group you'll see:
;;
;;      Canonical spelling for company 'daocloud': [DaoCloud] Daocloud
;;
;;    The bracketed item is the pre-selected default (first variant).
;;    Type to filter / select a different option, or press RET to accept.
;;    Pressing C-g skips the current group and moves on.
;;
;; 4. After all choices are made, fixes are applied to the file on disk and
;;    the buffer is reverted.  Re-run flycheck (C-c ! r) to verify.

(require 'flycheck-maintainer-consistency)   ; for flycheck-maintainer--find-script

;; ---------------------------------------------------------------------------
;; Internal helpers
;; ---------------------------------------------------------------------------

(defun cncf-fix--assert-csv-buffer ()
  "Signal a user-error unless the current buffer visits project-maintainers.csv."
  (unless (and buffer-file-name
               (string= (file-name-nondirectory buffer-file-name)
                        "project-maintainers.csv"))
    (user-error "cncf-fix: this command only works in project-maintainers.csv")))

(defun cncf-fix--gitdm-dir ()
  "Return the absolute path to the gitdm repo (sibling of the foundation repo), or nil."
  (when-let* ((root (locate-dominating-file default-directory ".github")))
    (let ((gitdm (expand-file-name "../gitdm" root)))
      (when (file-directory-p gitdm) gitdm))))

(defun cncf-fix--run (&rest args)
  "Run the consistency-checker Python script with ARGS.
The CSV file path is appended automatically as the final argument.
Passes --gitdm-dir when the gitdm sibling repo is found.
Returns stdout as a string, or signals an error on non-zero exit."
  (let* ((script (or (flycheck-maintainer--find-script)
                     (user-error "cncf-fix: checker script not found in this repo")))
         (csv    (expand-file-name buffer-file-name))
         (gitdm  (cncf-fix--gitdm-dir))
         (all-args (append args
                           (when gitdm (list "--gitdm-dir" gitdm))
                           (list csv))))
    (with-temp-buffer
      (let ((exit (apply #'call-process "python3" nil '(t nil) nil
                         script all-args)))
        (unless (zerop exit)
          (error "cncf-fix: script exited %d — %s" exit (buffer-string)))
        (buffer-string)))))

(defun cncf-fix--list-fixable ()
  "Return fixable issue groups as a list of alists parsed from JSON."
  (json-parse-string
   (cncf-fix--run "--list-fixable")
   :array-type  'list
   :object-type 'alist))

(defun cncf-fix--apply-one (field from to &optional handle)
  "Apply one substitution to the CSV file on disk.
FIELD is \"name\" or \"company\".
FROM is the current value; TO is the canonical replacement.
HANDLE, when non-nil, restricts changes to rows with that GitHub handle.
Returns the count of changed rows, or nil if the script output was unexpected."
  (let* ((extra  (when handle (list "--handle" handle)))
         (result (apply #'cncf-fix--run
                        "--apply-fix"
                        "--field" field
                        "--from"  from
                        "--to"    to
                        extra)))
    (when (string-match "\"changed\":[[:space:]]*\\([0-9]+\\)" result)
      (string-to-number (match-string 1 result)))))

(defun cncf-fix--annotate-company-variants (variants dates-alist)
  "Return an alist mapping annotated display strings to raw spellings.
Each variant is displayed as \"Spelling  (DD-MON-YYYY)\" when a date is
available, or just \"Spelling\" otherwise.
VARIANTS is a list of spelling strings.
DATES-ALIST is an alist of (spelling . date-string-or-nil) from the JSON."
  (mapcar (lambda (spelling)
            (let ((date (alist-get (intern spelling) dates-alist)))
              (cons (if (and date (not (eq date :null)))
                        (format "%-40s (%s)" spelling date)
                      spelling)
                    spelling)))
          variants))

(defun cncf-fix--goto-line (line-num)
  "Move point to LINE-NUM in the current buffer without changing the window."
  (goto-char (point-min))
  (forward-line (1- line-num)))

(defun cncf-fix--collect-choices (issues type-key field-key)
  "Prompt the user to pick a canonical value for each issue of TYPE-KEY.

Returns a list of (FIELD FROM TO HANDLE) tuples representing the fixes
to apply.  HANDLE is nil for company-casing fixes.  Issues where the user
presses C-g are skipped.

Before each completing-read the buffer scrolls to the first line involved
in that issue so the user has context.  For company and name issues, each
candidate is annotated with the git-blame date of its first occurrence."
  (let ((csv-buf (current-buffer))
        fixes)
    (dolist (issue issues)
      (when (string= (alist-get 'type issue) type-key)
        (let* ((prompt        (alist-get 'prompt        issue))
               (first-line    (alist-get 'first_line    issue))
               (variants      (append (alist-get 'variants issue) nil))
               (handle        (alist-get 'handle        issue))
               (dates-alist   (alist-get 'variant_dates issue))
               (gitdm-company (alist-get 'gitdm_company issue))
               (gitdm-from    (alist-get 'gitdm_from    issue))
               ;; Append gitdm canonical hint to the prompt when available.
               (gitdm-hint
                (when (and gitdm-company (not (eq gitdm-company :null)))
                  (if (and gitdm-from (not (eq gitdm-from :null)))
                      (format "  [gitdm → %s from %s]" gitdm-company gitdm-from)
                    (format "  [gitdm → %s]" gitdm-company))))
               (full-prompt   (format "%s%s" prompt (or gitdm-hint "")))
               (candidates    (if dates-alist
                                  (cncf-fix--annotate-company-variants
                                   variants dates-alist)
                                (mapcar (lambda (s) (cons s s)) variants)))
               (display-list  (mapcar #'car candidates))
               (chosen-display
                (condition-case nil
                    (progn
                      ;; Navigate to the first relevant line before prompting.
                      (when first-line
                        (with-current-buffer csv-buf
                          (cncf-fix--goto-line first-line)
                          (recenter)))
                      (completing-read
                       (format "%s: " full-prompt)
                       display-list
                       nil t
                       nil nil
                       (car display-list)))
                  (quit nil)))
               (canonical (when chosen-display
                            (cdr (assoc chosen-display candidates)))))
          (when canonical
            (dolist (variant variants)
              (unless (string= variant canonical)
                (push (list field-key variant canonical handle) fixes)))))))
    (nreverse fixes)))

;; ---------------------------------------------------------------------------
;; Public commands
;; ---------------------------------------------------------------------------

(defun cncf/fix-company-variants ()
  "Interactively pick the canonical company for all company inconsistencies.

Handles two kinds of issue in sequence:

  1. Casing variants — the same company name spelled with different
     capitalisation across the whole file (e.g. \"DaoCloud\" vs \"Daocloud\").

  2. Cross-project mismatches — a single GitHub handle is listed under
     genuinely different companies across projects (e.g. @superq appears
     under both 'Gitlab' and 'Reddit').  The git-blame date of each
     variant's first occurrence is shown alongside it so you can identify
     the most recently added entry.

For each group you are prompted via completing-read to choose the canonical
form.  All diverging occurrences (restricted to the handle for mismatches)
are rewritten in a single pass and the buffer is reverted.

Press RET to accept the pre-selected default, or type to choose another.
Press C-g to skip a particular group."
  (interactive)
  (cncf-fix--assert-csv-buffer)
  (let* ((issues (cncf-fix--list-fixable))
         (fixes  (append
                  (cncf-fix--collect-choices issues "company-casing"   "company")
                  (cncf-fix--collect-choices issues "company-mismatch" "company")))
         (total  0))
    (if (null fixes)
        (message "cncf: no company-name inconsistencies to fix.")
      (dolist (fix fixes)
        (cl-destructuring-bind (field from to handle) fix
          (let ((n (cncf-fix--apply-one field from to handle)))
            (when n (cl-incf total n)))))
      (revert-buffer nil t t)
      (message "cncf: fixed %d company-name occurrence(s). Re-run flycheck to verify."
               total))))

(defun cncf/fix-name-variants ()
  "Interactively pick the canonical given-name spelling for each handle with variants.

For every GitHub handle whose maintainer name is spelled differently across
projects you are prompted to choose one canonical spelling.  All diverging
occurrences for that handle are then replaced in a single pass.

Press RET to accept the pre-selected default, or type to choose another.
Press C-g to skip a particular handle."
  (interactive)
  (cncf-fix--assert-csv-buffer)
  (let* ((issues (cncf-fix--list-fixable))
         (fixes  (cncf-fix--collect-choices issues "name-mismatch" "name"))
         (total  0))
    (if (null fixes)
        (message "cncf: no given-name spelling issues to fix.")
      (dolist (fix fixes)
        (cl-destructuring-bind (field from to handle) fix
          (let ((n (cncf-fix--apply-one field from to handle)))
            (when n (cl-incf total n)))))
      (revert-buffer nil t t)
      (message "cncf: fixed %d given-name occurrence(s). Re-run flycheck to verify."
               total))))

(defun cncf/fix-all-variants ()
  "Fix all company-name and given-name inconsistencies interactively.
Runs `cncf/fix-company-variants' then `cncf/fix-name-variants'."
  (interactive)
  (cncf/fix-company-variants)
  (cncf/fix-name-variants))

(defun cncf/lookup-maintainer-company ()
  "Look up the GitHub handle on the current CSV row in the gitdm affiliation database.

Displays the maintainer's full company history in a dedicated
*CNCF Maintainer Lookup* buffer, including:
  - Current employer and the date they joined it
  - Complete chronological affiliation history with date ranges
  - Known email addresses recorded in gitdm

The gitdm repo must be present as a sibling directory of this repo
(i.e. ../gitdm relative to the foundation repo root).

Works from any row in project-maintainers.csv; if the row has no
GitHub handle (e.g. a header or blank line) a message is shown instead."
  (interactive)
  (cncf-fix--assert-csv-buffer)
  (let* ((line-num (line-number-at-pos))
         (handle   (string-trim
                    (cncf-fix--run "--handle-at-line"
                                   (number-to-string line-num)))))
    (if (string= handle "null")
        (message "cncf: no GitHub handle found on line %d" line-num)
      (let* ((raw  (cncf-fix--run "--lookup-handle" handle))
             (data (json-parse-string raw
                                      :object-type 'alist
                                      :array-type  'list)))
        (if (eq data :null)
            (message "cncf: @%s not found in the gitdm affiliation database" handle)
          (let ((buf (get-buffer-create "*CNCF Maintainer Lookup*")))
            (with-current-buffer buf
              (let ((inhibit-read-only t))
                (erase-buffer)
                ;; Header
                (insert (format "GitHub handle : @%s\n"
                                (alist-get 'handle data handle)))
                (let ((co   (alist-get 'current_company data))
                      (from (alist-get 'current_from    data)))
                  (insert "Current company: ")
                  (insert (if (and co (not (eq co :null))) co "Unknown"))
                  (when (and from (not (eq from :null)))
                    (insert (format "  (from %s)" from)))
                  (insert "\n"))
                ;; History
                (insert "\nAffiliation history:\n")
                (dolist (entry (alist-get 'history data))
                  (let ((co   (alist-get 'company entry))
                        (fr   (alist-get 'from    entry))
                        (un   (alist-get 'until   entry)))
                    (insert (format "  %-50s" (or co "?")))
                    (cond
                     ((and (not (eq fr :null)) fr
                           (not (eq un :null)) un)
                      (insert (format "%s – %s" fr un)))
                     ((and (not (eq fr :null)) fr)
                      (insert (format "%s →" fr)))
                     ((and (not (eq un :null)) un)
                      (insert (format "until %s" un))))
                    (insert "\n")))
                ;; Emails
                (insert "\nKnown emails:\n")
                (dolist (email (alist-get 'emails data))
                  (insert (format "  %s\n"
                                  (replace-regexp-in-string "!" "@" email))))
                (goto-char (point-min)))
              (special-mode)
              (setq-local revert-buffer-function
                          (lambda (_ignore-auto _noconfirm)
                            (cncf/lookup-maintainer-company))))
            (display-buffer buf
                            '((display-buffer-reuse-window
                               display-buffer-below-selected)
                              (window-height . 20)))))))))

;; ---------------------------------------------------------------------------
;; Key bindings — active only when visiting project-maintainers.csv
;; ---------------------------------------------------------------------------

(defun cncf-fix--setup-local-keys ()
  "Install buffer-local key bindings when visiting project-maintainers.csv."
  (when (and buffer-file-name
             (string= (file-name-nondirectory buffer-file-name)
                      "project-maintainers.csv"))
    (local-set-key (kbd "C-c f c") #'cncf/fix-company-variants)
    (local-set-key (kbd "C-c f n") #'cncf/fix-name-variants)
    (local-set-key (kbd "C-c f a") #'cncf/fix-all-variants)
    (local-set-key (kbd "C-c f l") #'cncf/lookup-maintainer-company)))

(add-hook 'csv-mode-hook #'cncf-fix--setup-local-keys)

(provide 'cncf-fix-variants)
;;; cncf-fix-variants.el ends here
