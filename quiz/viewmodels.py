#-*- coding: utf-8 -*-
from django.views.generic.list import ListView
from django.views.generic.edit import CreateView, UpdateView  # , DeleteView
from django.views.generic.detail import DetailView
from django.views.generic import TemplateView
from django.shortcuts import redirect, render

from quiz.forms import ParticipantForm, QuizForm
from quiz.models import Participant, Quiz, Lobby, UserAnswer
from quiz import control
import quizhub.lobby
import utils
from utils import api_guard





# ----------------------------------
# User
# ----------------------------------
class ParticipantMixin(object):
    model = Participant

    def get_context_data(self, **kw):
        kw.update({'object_name': 'Participant'})
        return kw


class ParticipantFormMixin(ParticipantMixin):
    formclass = ParticipantForm
    template_name = 'quiz/participants/form.html'


class UserList(ParticipantMixin, ListView):
    template_name = 'quiz/participants/list.html'


class ViewUser(ParticipantMixin, DetailView):
    pass


class CreateParticipant(ParticipantFormMixin, CreateView):
    template_name = 'quiz/participants/register.html'

    def post(self, request, *args, **kwargs):
        name = request.POST['name']
        if not request.session.exists(request.session.session_key):
            request.session.create()
        session_key = request.session.session_key
        part = Participant(name=name, session_key=session_key, django_user=None)
        part.save()
        request.session['uid'] = part.id
        request.session['name'] = name
        return redirect('mypage')


class UpdateParticipant(ParticipantFormMixin, UpdateView):
    pass


# ----------------------------------
# Quiz
# ----------------------------------
class QuizMixin(object):
    model = Quiz

    def get_context_data(self, **kw):
        kw.update({'object_name': Quiz})
        return kw


class QuizFormMixin(QuizMixin):
    formclass = QuizForm
    template_name = 'quiz/quiz/form.html'


class ViewQuiz(QuizMixin, DetailView):
    template_name = 'quiz/quiz/view.html'


class ListQuiz(QuizMixin, ListView):
    template_name = 'quiz/quiz/list.html'

    def get_context_data(self, **kw):
        kw['quizes'] = Quiz.objects.all()
        return kw


class CreateQuiz(QuizFormMixin, CreateView):
    pass


class UpdateQuiz(QuizFormMixin, UpdateView):
    pass


class ViewLobby(TemplateView):
    template_name = 'quiz/lobby/view.html'

    def get_context_data(self, **kw):
        uid = self.request.session['uid']
        participant = Participant.objects.get(pk=uid)
        lobby = Lobby.objects.get(pk=kw['lobby_id'])
        lobby.check_participant(participant)

        if lobby.active_quiz is None:
            kw['quiz'] = None
            kw['choices'] = None
        else:
            quiz_body = lobby.active_quiz.body
            kw['quiz'] = quiz_body
            kw['choices'] = quiz_body.answerchoice_set.all()
        kw.update(
            participant=participant,
            lobby=lobby,
        )
        return kw

    def get_score_list(self, uid):
        lobby = Lobby.objects.get(pk=self.kwargs['pk'])
        request_participant = Participant.objects.get(pk=uid)
        score_list = lobby.score_list

        def append_is_you_filter(entry):
            """スコアの要素を走査し，そのスコアがリクエストした人だった場合は is_you: True を足して
            そうで無ければ is_you: False を追加します
            """
            if entry['participant_id'] == request_participant.id:
                entry['is_you'] = True
            else:
                entry['is_you'] = False
            return entry

        return [append_is_you_filter(entry) for entry in score_list]

    def get_all_answers(self):
        lobby = Lobby.objects.get(pk=self.kwargs['pk'])
        return lobby.all_answers()

    def query_data(self, request, *args, **kw):
        command = request.GET.get('command')
        if command == 'score_list':
            uid = request.session['uid']
            return utils.JsonStatuses.ok(content=self.get_score_list(uid))
        elif command == 'all_answers':
            return utils.JsonStatuses.ok(content=self.get_all_answers())
        else:
            return utils.JsonStatuses.failed('Unknown command %s' % command)

    def get(self, request, *args, **kwargs):
        """GET

        クエリが無い場合は，quiz/lobby/view.htmlを用いてHTMLを返します
        ユーザーが登録していない場合は，登録ページへリダイレクトします

        クエリパラメータ?type=query があるときは，JSONを返します
        * ?type=query&command=score_list
            各参加者とそのスコアのリストを返します．
            返されるJSONは，スコアで降順ソートされており，
            同様にランキングで昇順ソートされています．このとき，スコアが同じ場合はランキングが変化しません．
            例:
                {'status': 'ok',
                 'content':
                    [{'score': 1,
                      'rank': 1,
                      'name': 'test_user1',
                      'participant_id': 1,
                      'is_you': True},
                     ....
                     ]}
            それぞれ
                score: 参加者の得点 :: 数値(たぶん整数値)
                rank: 参加者の順位(同率あり) :: 整数値
                name: 名前(文字列)
                participant_id: 内部で用いる参加者ID
                is_you: このリクエストを送った人のスコアの時True, そうでなければFalse
        * ?type=query&command=all_answers
            参加者の回答一覧を返します
            {'status': 'ok',
             'content': [
                {'name': 'test_user', 'participant_id': 1, 'choice_id': 1},
                ....
             ]}
        """
        try:
            if request.GET.get('type') == 'query':
                return self.query_data(request, *args, **kwargs)
            context = self.get_context_data(
                lobby_id=kwargs['pk']
            )
            return self.render_to_response(context)
        except Participant.DoesNotExist:
            return redirect('participant_register')

    def post(self, request, *args, **kw):
        command = request.POST.get('command')
        if command == 'submit_answer':
            lobby_id = request.POST['lobby_id']
            context = self.get_context_data(lobby_id=lobby_id)
            lobby = context['lobby']
            participant = context['participant']
            active_quiz = lobby.active_quiz
            if not lobby.can_accept_answer(participant):
                return utils.JsonStatuses.failed('Closed')
            if 'choice_id' not in request.POST:
                return utils.JsonStatuses.failed('Invalid POST: choice_id required.')
            choice_id = request.POST['choice_id']
            ans = UserAnswer(
                quiz=active_quiz,
                choice=int(choice_id),
                user=context['participant'],
            )
            ans.save()
            return utils.JsonStatuses.ok()
        else:
            return utils.JsonStatuses.failed('Unknown command')


class ControlLobby(TemplateView):
    template_name = 'quiz/lobby/control.html'

    def get(self, req, *args, **kw):
        lobby = Lobby.objects.get(pk=kw['pk'])
        return render(req, self.template_name, {
            'lobby': lobby,
        })

    @api_guard
    def post(self, req, *args, **kw):
        lobby = Lobby.objects.get(pk=kw['pk'])
        command = req.POST['command']
        if command == 'activate':
            lobby.initialize(force=True)
        elif command == 'start_quiz':
            lobby.go_next_quiz()
        elif command == 'close_submission':
            lobby.close_participant_submission()
        elif command == 'show_scores':
            lobby.close_master_submission()
        elif command == 'next':
            lobby.go_next_quiz()
            if lobby.is_finished:
                return utils.JsonStatuses.failed('Already closed!')
        else:
            return utils.JsonStatuses.failed('Unknown command!')
        quizhub.lobby.lobby_hub.request_update()
        return utils.JsonStatuses.ok()


class ActiveLobbyView(TemplateView):
    template_name = 'quiz/lobby/view.html'

    def get(self, request, *args, **kwargs):
        active_lobby = control.active_lobby.get()
        if active_lobby is not None:
            return redirect('lobby_show', active_lobby.pk)
        else:
            return render(request, 'quiz/lobby/nolobby.html')

    def post(self, req, *args, **kw):
        activating_id = req.POST['id']
        if activating_id == 'default':
            lobby = Lobby.objects.filter(started_time=None).first()
        else:
            lobby = Lobby.objects.get(pk=activating_id)
        control.active_lobby.set(lobby)
        return utils.JsonStatuses.ok(message='Active lobby set to Lobby %d' % lobby.pk)


