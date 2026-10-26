"""Portable filesystem setup for security checks in temporary workspaces."""
import errno


def make_symlink(case, link, target, directory=False):
    """Skip only when this host forbids links; real fixture errors still fail."""
    try:
        link.symlink_to(target, target_is_directory=directory)
    except NotImplementedError as error:
        case.skipTest('Host does not implement test symlinks: ' + str(error))
    except OSError as error:
        unsupported = (errno.EPERM, errno.EACCES, errno.ENOSYS,
                       getattr(errno, 'ENOTSUP', -1),
                       getattr(errno, 'EOPNOTSUPP', -1))
        if error.errno in unsupported or getattr(error, 'winerror', None) == 1314:
            case.skipTest('Host cannot create test symlinks: ' + str(error))
        raise
