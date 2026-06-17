;;; flycheck-maintainer-consistency.el --- flycheck checker for project-maintainers.csv

;; Add to Doom Emacs config.el:
;;
;;   (after! flycheck
;;     (load! "~/.../foundation/.github/scripts/flycheck-maintainer-consistency"))
;;
;; Or if the repo is open as a project:
;;
;;   (after! flycheck
;;     (load! (expand-file-name ".github/scripts/flycheck-maintainer-consistency"
;;                              (projectile-project-root))))

(require 'flycheck)

(defun flycheck-maintainer--find-script ()
  "Walk up from the current buffer's directory to find the checker script.
Returns the absolute path as a string, or nil if not found."
  (when-let* ((root (locate-dominating-file default-directory ".github")))
    (let ((script (expand-file-name
                   ".github/scripts/check_maintainer_consistency.py"
                   root)))
      ;; Use file-exists-p — the script is invoked via `python3 script.py`,
      ;; so the executable bit is not required.
      (when (file-exists-p script) script))))

(flycheck-define-checker cncf-maintainer-csv
  "Consistency checker for the CNCF project-maintainers.csv file.

Errors (shown with error face) must be fixed before a PR is merged:
  - Duplicate GitHub handle within the same project
  - Malformed handle (@ prefix, spaces, invalid characters)
  - Missing name field

Warnings (shown with warning face) flag data that may be stale:
  - Same handle with different name spelling across projects
  - Same handle with different company across projects
  - Company name capitalisation variants
  - Same person's name associated with two different handles"
  :command ("python3"
            (eval (or (flycheck-maintainer--find-script)
                      (error "cncf-maintainer-csv: checker script not found; \
is this buffer inside the foundation repo?")))
            "--flycheck"
            source-original)
  :error-patterns
  ((error   line-start (file-name) ":" line ": error: "   (message) line-end)
   (warning line-start (file-name) ":" line ": warning: " (message) line-end))
  :modes (csv-mode)
  :predicate (lambda ()
               (and buffer-file-name
                    (string= (file-name-nondirectory buffer-file-name)
                             "project-maintainers.csv")
                    ;; Disable silently if the script can't be located
                    ;; (e.g. a different CSV file in an unrelated repo).
                    (flycheck-maintainer--find-script))))

(add-to-list 'flycheck-checkers 'cncf-maintainer-csv)

(provide 'flycheck-maintainer-consistency)
;;; flycheck-maintainer-consistency.el ends here
