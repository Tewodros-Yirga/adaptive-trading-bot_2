import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { UserPlus, Trash2, Shield, ShieldCheck, Eye, EyeOff } from 'lucide-react'
import { getUsers, createUser, updateUser, deleteUser } from '../api'
import { Card, SectionHeader, Btn, Badge, ConfirmModal, Spinner } from '../components'
import { useAppStore } from '../store'

export default function UsersPage() {
  const qc = useQueryClient()
  const { addToast, user: currentUser } = useAppStore()
  const [showCreate, setShowCreate] = useState(false)
  const [newUser, setNewUser] = useState({ username: '', password: '', role: 'viewer', full_access: false })
  const [deleteTarget, setDeleteTarget] = useState<any>(null)
  const [showPassword, setShowPassword] = useState(false)

  const { data: users, isLoading } = useQuery({ queryKey: ['users'], queryFn: getUsers })

  const createMut = useMutation({
    mutationFn: () => createUser(newUser),
    onSuccess: () => {
      addToast('success', `User '${newUser.username}' created`)
      setNewUser({ username: '', password: '', role: 'viewer', full_access: false })
      setShowCreate(false)
      qc.invalidateQueries({ queryKey: ['users'] })
    },
    onError: (e: any) => addToast('error', e.message),
  })

  const updateMut = useMutation({
    mutationFn: ({ id, data }: any) => updateUser(id, data),
    onSuccess: () => { addToast('success', 'User updated'); qc.invalidateQueries({ queryKey: ['users'] }) },
    onError: (e: any) => addToast('error', e.message),
  })

  const deleteMut = useMutation({
    mutationFn: (id: number) => deleteUser(id),
    onSuccess: () => { addToast('success', 'User deleted'); qc.invalidateQueries({ queryKey: ['users'] }) },
    onError: (e: any) => addToast('error', e.message),
  })

  if (isLoading) return <div className="p-6 flex justify-center"><Spinner /></div>

  return (
    <div className="p-6 fade-in">
      <SectionHeader title="User Management" sub="Create and manage user accounts" />

      {/* Create user button */}
      <div className="mb-4">
        <Btn onClick={() => setShowCreate(!showCreate)} variant={showCreate ? 'ghost' : 'default'}>
          <UserPlus size={14} /> {showCreate ? 'Cancel' : 'Create User'}
        </Btn>
      </div>

      {/* Create form */}
      {showCreate && (
        <Card className="mb-6">
          <p className="font-medium text-sm mb-4">New User</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted">Username</label>
              <input
                type="text"
                value={newUser.username}
                onChange={e => setNewUser(u => ({ ...u, username: e.target.value }))}
                className="bg-bg border border-border rounded px-3 py-1.5 text-sm text-white focus:border-accent outline-none"
                placeholder="username"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted">Password</label>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={newUser.password}
                  onChange={e => setNewUser(u => ({ ...u, password: e.target.value }))}
                  className="bg-bg border border-border rounded px-3 py-1.5 text-sm text-white focus:border-accent outline-none w-full pr-8"
                  placeholder="password"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-white"
                >
                  {showPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted">Role</label>
              <select
                value={newUser.role}
                onChange={e => setNewUser(u => ({ ...u, role: e.target.value }))}
                className="bg-bg border border-border rounded px-3 py-1.5 text-sm text-white focus:border-accent outline-none"
              >
                <option value="viewer">Viewer</option>
                <option value="admin">Admin</option>
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted">Full Access</label>
              <label className="flex items-center gap-2 h-9">
                <input
                  type="checkbox"
                  checked={newUser.full_access}
                  onChange={e => setNewUser(u => ({ ...u, full_access: e.target.checked }))}
                  className="cursor-pointer accent-accent"
                />
                <span className="text-sm text-muted">Grant write access</span>
              </label>
            </div>
          </div>
          <Btn
            onClick={() => createMut.mutate()}
            disabled={!newUser.username || !newUser.password || createMut.isPending}
          >
            <UserPlus size={14} /> Create User
          </Btn>
        </Card>
      )}

      {/* Users table */}
      <Card>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-muted border-b border-border text-xs">
                <th className="text-left py-2 pr-4">ID</th>
                <th className="text-left py-2 pr-4">Username</th>
                <th className="text-left py-2 pr-4">Role</th>
                <th className="text-left py-2 pr-4">Full Access</th>
                <th className="text-left py-2 pr-4">Status</th>
                <th className="text-left py-2 pr-4">Created</th>
                <th className="text-left py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {(users || []).map((u: any) => (
                <tr key={u.id} className="border-b border-border/50 hover:bg-white/5 transition-colors">
                  <td className="py-3 pr-4 mono text-muted">{u.id}</td>
                  <td className="py-3 pr-4 font-medium">
                    {u.username}
                    {u.id === currentUser?.id && (
                      <span className="ml-2 text-xs text-accent">(you)</span>
                    )}
                  </td>
                  <td className="py-3 pr-4">
                    <Badge
                      label={u.role.toUpperCase()}
                      color={u.role === 'admin' ? 'bg-accent/20 text-accent' : 'bg-muted/20 text-muted'}
                    />
                  </td>
                  <td className="py-3 pr-4">
                    <button
                      onClick={() => updateMut.mutate({ id: u.id, data: { full_access: !u.full_access } })}
                      disabled={u.role === 'admin' || u.id === currentUser?.id}
                      className={`flex items-center gap-1 text-xs px-2 py-1 rounded transition-colors ${
                        u.full_access
                          ? 'bg-success/20 text-success hover:bg-success/30'
                          : 'bg-border text-muted hover:bg-border/80'
                      } disabled:opacity-50 disabled:cursor-not-allowed`}
                    >
                      {u.full_access ? <ShieldCheck size={12} /> : <Shield size={12} />}
                      {u.full_access ? 'Granted' : 'Restricted'}
                    </button>
                  </td>
                  <td className="py-3 pr-4">
                    <button
                      onClick={() => updateMut.mutate({ id: u.id, data: { is_active: !u.is_active } })}
                      disabled={u.id === currentUser?.id}
                      className={`text-xs px-2 py-1 rounded transition-colors ${
                        u.is_active
                          ? 'bg-success/20 text-success hover:bg-success/30'
                          : 'bg-danger/20 text-danger hover:bg-danger/30'
                      } disabled:opacity-50 disabled:cursor-not-allowed`}
                    >
                      {u.is_active ? 'Active' : 'Disabled'}
                    </button>
                  </td>
                  <td className="py-3 pr-4 text-xs text-muted">
                    {u.created_at?.slice(0, 10)}
                  </td>
                  <td className="py-3">
                    {u.id !== currentUser?.id && (
                      <button
                        onClick={() => setDeleteTarget(u)}
                        className="text-muted hover:text-danger transition-colors p-1"
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {(!users || users.length === 0) && (
                <tr><td colSpan={7} className="text-center text-muted py-8">No users</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {deleteTarget && (
        <ConfirmModal
          title={`Delete user "${deleteTarget.username}"?`}
          message="This action cannot be undone. The user will lose all access."
          onConfirm={() => { deleteMut.mutate(deleteTarget.id); setDeleteTarget(null) }}
          onCancel={() => setDeleteTarget(null)}
          variant="danger"
        />
      )}
    </div>
  )
}
